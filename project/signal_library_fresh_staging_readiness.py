"""Phase 6I-32: supervised fresh-source readiness + staged
signal-library rebuild evidence harness.

Read-only coordinator. Runs the existing source/cache probes
+ Phase 6I-30 sandbox builder + Phase 6I-31 promotion planner
+ Phase 6I-31 promotion writer dry-run + Phase 6I-22..27
multi-window K chain against a sandbox-built staged dir +
production-root snapshot diff. Classifies the operator-facing
verdict as one of:

  STATE_SOURCE_NOT_READY          -- the source/cache predicate
                                     is not strictly cache-
                                     ahead-of-cutoff for every
                                     inspected ticker. The
                                     harness still captures
                                     downstream evidence so an
                                     auditor can see what the
                                     full chain WOULD say if
                                     source/cache were ready,
                                     but the final verdict is
                                     STATE_A regardless. The
                                     operator next step is to
                                     refresh the source cache
                                     (out of scope for this
                                     module; that requires the
                                     Phase 6E-5 refresher under
                                     its own supervised gate).

  STATE_STAGED_REBUILD_NOT_READY  -- source/cache is ready but
                                     the staged rebuild itself
                                     failed: sandbox builder
                                     skipped some files, OR the
                                     promotion planner reports
                                     plan_ready=False, OR the
                                     multi-window K adapter
                                     can not reach 60/60, OR
                                     the production-root
                                     snapshot diff is non-zero.
                                     The operator next step is
                                     to read the surfaced
                                     issue codes per stage.

  STATE_STAGED_REBUILD_READY      -- everything green at
                                     dry-run: source/cache
                                     ready, sandbox build
                                     produced every requested
                                     library, promotion planner
                                     plan_ready=true, promotion
                                     writer dry-run blocked on
                                     auth gates only, adapter
                                     prepared 60/60, payload
                                     builder payload_ready=true,
                                     patch planner patch_ready=
                                     true, patch writer dry-run
                                     planner_patch_ready=true,
                                     production-root diff 0/0/0.
                                     The operator next step is
                                     to review this evidence
                                     and decide whether to
                                     authorize the Phase 6I-31
                                     production promotion in
                                     a separate prompt.

What this module IS NOT
-----------------------

NOT a writer. NOT a refresher. NOT an authorizer. NOT a
pipeline runner. NOT a batch engine. NOT a yfinance fetcher
at top level -- the source-availability probe is reached only
through an injection seam whose default delegates to
``source_availability_probe.evaluate_source_availability``
(which in turn calls the Phase 6E-5 refresher with
``write=False``, the established read-only probe pattern).
Phase 6I-32 NEVER sets ``PRJCT9_AUTOMATION_WRITE_AUTH`` and
NEVER passes ``--write`` to any downstream writer.

Public surface
--------------

    DEFAULT_INTERVALS
    DEFAULT_CANONICAL_K_VALUES

    STATE_SOURCE_NOT_READY
    STATE_STAGED_REBUILD_NOT_READY
    STATE_STAGED_REBUILD_READY

    ISSUE_SOURCE_CACHE_NOT_READY
    ISSUE_SANDBOX_BUILD_INCOMPLETE
    ISSUE_PROMOTION_PLAN_NOT_READY
    ISSUE_ADAPTER_NOT_FULL_GRID
    ISSUE_PAYLOAD_NOT_READY
    ISSUE_PATCH_PLAN_NOT_READY
    ISSUE_PRODUCTION_ROOT_DRIFT_DETECTED
    ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE

    @dataclass FreshStagingReadinessReport

    evaluate_fresh_staging_readiness(
        target_tickers, *,
        staged_dir,
        cache_dir,
        stackbuilder_root,
        production_stable_dir,
        confluence_artifact_root,
        current_as_of_date=None,
        intervals=DEFAULT_INTERVALS,
        cache_cutoff_probe_callable=None,
        source_availability_probe_callable=None,
        sandbox_builder_callable=None,
        promotion_planner_callable=None,
        promotion_writer_callable=None,
        adapter_diagnostic_callable=None,
        payload_builder_callable=None,
        patch_planner_callable=None,
        patch_writer_callable=None,
        production_snapshot_callable=None,
        production_diff_callable=None,
        run_source_availability=True,
        run_downstream_chain=True,
        run_snapshot_diff=True,
    ) -> FreshStagingReadinessReport
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


# Default canonical surface re-exported for CLI convenience.
DEFAULT_INTERVALS: tuple[str, ...] = (
    "1d", "1wk", "1mo", "3mo", "1y",
)
DEFAULT_CANONICAL_K_VALUES: tuple[int, ...] = tuple(
    range(1, 13),
)


# ---------------------------------------------------------------------------
# State + issue codes
# ---------------------------------------------------------------------------

STATE_SOURCE_NOT_READY = "source_not_ready"
STATE_STAGED_REBUILD_NOT_READY = "staged_rebuild_not_ready"
STATE_STAGED_REBUILD_READY = "staged_rebuild_ready"

ALL_STATES: tuple[str, ...] = (
    STATE_SOURCE_NOT_READY,
    STATE_STAGED_REBUILD_NOT_READY,
    STATE_STAGED_REBUILD_READY,
)


ISSUE_SOURCE_CACHE_NOT_READY = "source_cache_not_ready"
ISSUE_SANDBOX_BUILD_INCOMPLETE = "sandbox_build_incomplete"
ISSUE_PROMOTION_PLAN_NOT_READY = "promotion_plan_not_ready"
ISSUE_ADAPTER_NOT_FULL_GRID = "adapter_not_full_grid"
ISSUE_PAYLOAD_NOT_READY = "payload_not_ready"
ISSUE_PATCH_PLAN_NOT_READY = "patch_plan_not_ready"
ISSUE_PRODUCTION_ROOT_DRIFT_DETECTED = (
    "production_root_drift_detected"
)
ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE = (
    "staged_dir_under_production_stable"
)

ALL_ISSUE_CODES: tuple[str, ...] = (
    ISSUE_SOURCE_CACHE_NOT_READY,
    ISSUE_SANDBOX_BUILD_INCOMPLETE,
    ISSUE_PROMOTION_PLAN_NOT_READY,
    ISSUE_ADAPTER_NOT_FULL_GRID,
    ISSUE_PAYLOAD_NOT_READY,
    ISSUE_PATCH_PLAN_NOT_READY,
    ISSUE_PRODUCTION_ROOT_DRIFT_DETECTED,
    ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE,
)


# ---------------------------------------------------------------------------
# Path-guard suffix shared with the Phase 6I-31 planner/writer.
# ---------------------------------------------------------------------------

_PRODUCTION_STABLE_SUFFIX: tuple[str, ...] = (
    "signal_library", "data", "stable",
)


def _path_is_under_production_stable(
    candidate: Any,
    production_stable_dir: Any = None,
) -> bool:
    """Return ``True`` if ``candidate`` is unsafe for sandbox
    writes because it would land at or under the production
    stable signal-library directory.

    Phase 6I-32 amendment-1 (rejects the original guard's
    suffix-only check that missed child paths like
    ``signal_library/data/stable/staged_libs``).

    A path is unsafe when:

      1. ``candidate`` equals ``production_stable_dir``
         (when supplied) after resolution; OR
      2. ``candidate`` is anywhere UNDER
         ``production_stable_dir`` (when supplied); OR
      3. ``candidate``'s resolved components contain
         ``signal_library/data/stable`` as a CONTIGUOUS
         ancestor segment (regardless of where in the path
         it sits). This catches paths that resolve under a
         signal_library/data/stable root even when an
         explicit ``production_stable_dir`` was not threaded
         through.

    The check is conservative on purpose: false positives
    are operator-recoverable (rename the staged dir);
    false negatives could let staged writes land in
    production.
    """
    try:
        cand = Path(candidate).resolve()
    except Exception:
        # Unresolvable candidate -> assume unsafe.
        return True

    if production_stable_dir is not None:
        try:
            prod = Path(production_stable_dir).resolve()
            if cand == prod:
                return True
            try:
                cand.relative_to(prod)
                return True
            except ValueError:
                pass
        except Exception:
            pass

    parts = [p.lower() for p in cand.parts]
    suffix = [
        p.lower() for p in _PRODUCTION_STABLE_SUFFIX
    ]
    if len(parts) < len(suffix):
        return False
    for i in range(len(parts) - len(suffix) + 1):
        if parts[i:i + len(suffix)] == suffix:
            return True
    return False


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class FreshStagingReadinessReport:
    generated_at: str
    state: str
    issue_codes: tuple[str, ...]
    target_tickers: tuple[str, ...]
    intervals: tuple[str, ...]
    staged_dir: str
    production_stable_dir: str
    confluence_artifact_root: str

    # Source/cache state
    source_cache_ready: bool
    cache_cutoff_summary: Optional[dict[str, Any]]
    source_availability_summary: Optional[dict[str, Any]]

    # Sandbox staged build
    sandbox_build_attempted: bool
    sandbox_build_written: int
    sandbox_build_failed: int

    # Promotion planner
    promotion_plan_summary: Optional[dict[str, Any]]
    promotion_plan_ready: Optional[bool]

    # Promotion writer dry-run
    promotion_writer_dry_run_summary: Optional[dict[str, Any]]

    # Downstream multi-window K chain
    adapter_diagnostic_summary: Optional[dict[str, Any]]
    payload_builder_summary: Optional[dict[str, Any]]
    patch_planner_summary: Optional[dict[str, Any]]
    patch_writer_dry_run_summary: Optional[dict[str, Any]]

    # Production-root snapshot
    production_snapshot_before: Optional[dict[str, Any]]
    production_snapshot_after: Optional[dict[str, Any]]
    production_root_diff: Optional[dict[str, Any]]

    recommended_next_action: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "state": self.state,
            "issue_codes": list(self.issue_codes),
            "target_tickers": list(self.target_tickers),
            "intervals": list(self.intervals),
            "staged_dir": self.staged_dir,
            "production_stable_dir": self.production_stable_dir,
            "confluence_artifact_root": (
                self.confluence_artifact_root
            ),
            "source_cache_ready": bool(self.source_cache_ready),
            "cache_cutoff_summary": self.cache_cutoff_summary,
            "source_availability_summary": (
                self.source_availability_summary
            ),
            "sandbox_build_attempted": bool(
                self.sandbox_build_attempted,
            ),
            "sandbox_build_written": int(
                self.sandbox_build_written,
            ),
            "sandbox_build_failed": int(
                self.sandbox_build_failed,
            ),
            "promotion_plan_summary": self.promotion_plan_summary,
            "promotion_plan_ready": (
                None if self.promotion_plan_ready is None
                else bool(self.promotion_plan_ready)
            ),
            "promotion_writer_dry_run_summary": (
                self.promotion_writer_dry_run_summary
            ),
            "adapter_diagnostic_summary": (
                self.adapter_diagnostic_summary
            ),
            "payload_builder_summary": (
                self.payload_builder_summary
            ),
            "patch_planner_summary": self.patch_planner_summary,
            "patch_writer_dry_run_summary": (
                self.patch_writer_dry_run_summary
            ),
            "production_snapshot_before": (
                self.production_snapshot_before
            ),
            "production_snapshot_after": (
                self.production_snapshot_after
            ),
            "production_root_diff": self.production_root_diff,
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


def _append_unique(buf: list[str], code: str) -> None:
    if code and code not in buf:
        buf.append(code)


# ---------------------------------------------------------------------------
# Default seam implementations (deferred imports so a test that
# fakes them never pays the import cost of yfinance / live modules)
# ---------------------------------------------------------------------------


def _default_cache_cutoff_probe(
    tickers: list[str],
    *,
    cache_dir: Path,
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
    cache_dir: Path,
    current_as_of_date: Optional[str],
) -> dict[str, Any]:
    import source_availability_probe as _sap  # local import
    report = _sap.evaluate_source_availability_many(
        tickers,
        cache_dir=cache_dir,
        current_as_of_date=current_as_of_date,
    )
    return report.to_json_dict()


def _default_sandbox_builder(
    tickers: list[str],
    *,
    intervals: list[str],
    cache_dir: Path,
    staged_dir: Path,
    end_date: Optional[str],
) -> dict[str, Any]:
    # Phase 6I-32 amendment-1 defense-in-depth: even though
    # the harness's outer guard already short-circuits the
    # sandbox call when staged_dir is unsafe, re-check here
    # so a future call-path mistake -- or an out-of-band
    # caller that uses this helper directly -- cannot write
    # under the production stable root. The
    # ``build_sandbox_libraries_for_ticker`` API does not
    # itself run the sandbox CLI's path guard (the CLI guard
    # runs in ``multi_timeframe_sandbox_builder.main``).
    if _path_is_under_production_stable(staged_dir):
        raise ValueError(
            "refusing to write sandbox libraries under "
            f"signal_library/data/stable: {staged_dir!r}"
        )
    from signal_library import (
        multi_timeframe_sandbox_builder as _sb,
    )
    written: list[str] = []
    failed: list[str] = []
    for ticker in tickers:
        per = _sb.build_sandbox_libraries_for_ticker(
            ticker, intervals,
            cache_dir=cache_dir,
            output_dir=staged_dir,
            end_date=end_date,
        )
        for interval, path in per.items():
            if path is None:
                failed.append(f"{ticker}|{interval}")
            else:
                written.append(str(path))
    return {
        "written": written,
        "failed": failed,
    }


def _default_promotion_planner(
    tickers: list[str],
    *,
    staged_dir: Path,
    production_stable_dir: Path,
    intervals: list[str],
) -> dict[str, Any]:
    import signal_library_stable_promotion_planner as _pp
    plan = _pp.plan_signal_library_stable_promotion(
        tickers,
        staged_dir=staged_dir,
        production_stable_dir=production_stable_dir,
        intervals=intervals,
    )
    return plan.to_json_dict()


def _default_promotion_writer(
    tickers: list[str],
    *,
    staged_dir: Path,
    production_stable_dir: Path,
    intervals: list[str],
    execution_log: Optional[Path],
) -> dict[str, Any]:
    import signal_library_stable_promotion_writer as _pw
    # write=False ALWAYS in this harness. Phase 6I-32 does
    # NOT authorize promotion mutation.
    result = _pw.promote_signal_libraries(
        tickers,
        staged_dir=staged_dir,
        production_stable_dir=production_stable_dir,
        intervals=intervals,
        write=False,
        execution_log=execution_log,
    )
    return result.to_json_dict()


def _default_adapter_diagnostic(
    ticker: str,
    *,
    stackbuilder_root: Path,
    signal_library_dir: Path,
    cache_dir: Path,
) -> dict[str, Any]:
    import multiwindow_k_input_adapter_diagnostic as _diag
    return _diag.run_adapter_diagnostic(
        ticker,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        cache_dir=cache_dir,
    )


def _default_payload_builder(
    ticker: str,
    *,
    stackbuilder_root: Path,
    signal_library_dir: Path,
    cache_dir: Path,
) -> dict[str, Any]:
    import multiwindow_k_engine_payload_builder as _pb
    report = _pb.build_multiwindow_k_engine_payload(
        ticker,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        close_source_root=cache_dir,
    )
    return report.to_json_dict()


def _default_patch_planner(
    ticker: str,
    *,
    artifact_root: Path,
    stackbuilder_root: Path,
    signal_library_dir: Path,
    cache_dir: Path,
    current_as_of_date: Optional[str],
) -> dict[str, Any]:
    import multiwindow_k_confluence_patch_planner as _pp
    plan = _pp.plan_multiwindow_k_confluence_patch(
        ticker,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        close_source_root=cache_dir,
        current_as_of_date=current_as_of_date,
    )
    return plan.to_json_dict()


def _default_patch_writer(
    ticker: str,
    *,
    artifact_root: Path,
    stackbuilder_root: Path,
    signal_library_dir: Path,
    cache_dir: Path,
    current_as_of_date: Optional[str],
    execution_log: Optional[Path],
) -> dict[str, Any]:
    import multiwindow_k_confluence_patch_writer as _pw
    # write=False ALWAYS in this harness. Phase 6I-32 does
    # NOT authorize artifact mutation.
    result = _pw.apply_multiwindow_k_confluence_patch(
        ticker,
        artifact_root=artifact_root,
        stackbuilder_root=stackbuilder_root,
        signal_library_dir=signal_library_dir,
        close_source_root=cache_dir,
        current_as_of_date=current_as_of_date,
        write=False,
        execution_log=execution_log,
    )
    return result.to_json_dict()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate_fresh_staging_readiness(
    target_tickers: Iterable[str],
    *,
    staged_dir: Any,
    cache_dir: Any,
    stackbuilder_root: Any,
    production_stable_dir: Any,
    confluence_artifact_root: Any,
    current_as_of_date: Optional[str] = None,
    intervals: Iterable[str] = DEFAULT_INTERVALS,
    sandbox_end_date: Optional[str] = None,
    primary_target_ticker: Optional[str] = None,
    cache_cutoff_probe_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    source_availability_probe_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    sandbox_builder_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    promotion_planner_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    promotion_writer_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    adapter_diagnostic_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    payload_builder_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    patch_planner_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    patch_writer_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    production_snapshot_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    production_diff_callable: Optional[
        Callable[..., dict[str, Any]]
    ] = None,
    promotion_writer_execution_log: Optional[Any] = None,
    patch_writer_execution_log: Optional[Any] = None,
    run_source_availability: bool = True,
    run_downstream_chain: bool = True,
    run_snapshot_diff: bool = True,
) -> FreshStagingReadinessReport:
    """Run the Phase 6I-32 supervised fresh-staging readiness
    harness. Read-only. Default callables delegate to the
    existing project modules; tests override via the kwargs.

    All seams may be overridden per-call so a test can plug in
    fakes without touching the real probes / builders /
    writers / snapshot helpers.
    """
    target_tickers_tuple = tuple(
        str(t).strip().upper() for t in target_tickers
        if str(t).strip()
    )
    interval_list = tuple(str(i).strip() for i in intervals)
    staged_path = Path(staged_dir)
    cache_path = Path(cache_dir)
    stack_path = Path(stackbuilder_root)
    prod_stable_path = Path(production_stable_dir)
    artifact_path = Path(confluence_artifact_root)
    primary = (
        primary_target_ticker
        or (target_tickers_tuple[0] if target_tickers_tuple else "")
    )

    issues: list[str] = []

    # Safety gate: refuse staged_dir that resolves at or under
    # the production stable signal-library directory. The
    # check covers (a) exact match with production_stable_dir,
    # (b) any path under production_stable_dir, AND (c) any
    # path containing signal_library/data/stable as a
    # contiguous segment -- so child paths like
    # signal_library/data/stable/staged_libs are caught even
    # when production_stable_dir was not threaded through.
    unsafe_staged_dir = _path_is_under_production_stable(
        staged_path, prod_stable_path,
    )
    if unsafe_staged_dir:
        _append_unique(
            issues, ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE,
        )

    # ----------------------------------------------------- 1. source / cache
    cache_probe = (
        cache_cutoff_probe_callable
        or _default_cache_cutoff_probe
    )
    try:
        cache_cutoff_summary = cache_probe(
            list(target_tickers_tuple),
            cache_dir=cache_path,
            current_as_of_date=current_as_of_date,
        )
    except Exception as exc:
        cache_cutoff_summary = {
            "error": "cache_cutoff_probe_failed",
            "detail": str(exc),
        }

    ready_tickers = set(
        (cache_cutoff_summary or {}).get(
            "ready_tickers", [],
        )
    )
    all_cache_ahead = bool(
        target_tickers_tuple
        and all(
            t in ready_tickers for t in target_tickers_tuple
        )
    )

    source_availability_summary: Optional[dict[str, Any]] = None
    if run_source_availability:
        sa_probe = (
            source_availability_probe_callable
            or _default_source_availability_probe
        )
        try:
            source_availability_summary = sa_probe(
                list(target_tickers_tuple),
                cache_dir=cache_path,
                current_as_of_date=current_as_of_date,
            )
        except Exception as exc:
            source_availability_summary = {
                "error": "source_availability_probe_failed",
                "detail": str(exc),
            }

    source_cache_ready = all_cache_ahead
    if not source_cache_ready:
        _append_unique(
            issues, ISSUE_SOURCE_CACHE_NOT_READY,
        )

    # ----------------------------------------------------- 2. snapshot before
    snapshot_helper = (
        production_snapshot_callable
        or _default_production_snapshot
    )
    snapshot_before: Optional[dict[str, Any]] = None
    if run_snapshot_diff:
        try:
            snapshot_before = snapshot_helper()
        except Exception as exc:
            snapshot_before = {
                "error": "snapshot_helper_failed",
                "detail": str(exc),
            }

    # ----------------------------------------------------- 3. sandbox build
    # Phase 6I-32 amendment-1: hard-stop the sandbox call when
    # staged_dir is unsafe. The check above set
    # ``unsafe_staged_dir`` BEFORE any disk-touching stage; if
    # it's True we refuse to invoke the sandbox builder
    # callable AT ALL, regardless of whether the caller
    # supplied a fake. This is the operator-safety contract:
    # no path that even THEORETICALLY resolves under the
    # production stable root gets a sandbox builder call.
    sandbox_attempted = not unsafe_staged_dir
    sandbox_written = 0
    sandbox_failed = 0
    if sandbox_attempted:
        sandbox_fn = (
            sandbox_builder_callable
            or _default_sandbox_builder
        )
        try:
            sandbox_result = sandbox_fn(
                list(target_tickers_tuple),
                intervals=list(interval_list),
                cache_dir=cache_path,
                staged_dir=staged_path,
                end_date=sandbox_end_date,
            )
            sandbox_written = len(
                sandbox_result.get("written", []) or [],
            )
            sandbox_failed = len(
                sandbox_result.get("failed", []) or [],
            )
        except Exception as exc:
            sandbox_result = {
                "error": "sandbox_builder_failed",
                "detail": str(exc),
            }
            sandbox_written = 0
            sandbox_failed = (
                len(target_tickers_tuple) * len(interval_list)
            )

        if sandbox_failed:
            _append_unique(
                issues, ISSUE_SANDBOX_BUILD_INCOMPLETE,
            )

    # ----------------------------------------------------- 4. promotion planner
    promotion_plan_summary: Optional[dict[str, Any]] = None
    promotion_plan_ready: Optional[bool] = None
    if sandbox_written > 0 and not unsafe_staged_dir:
        planner_fn = (
            promotion_planner_callable
            or _default_promotion_planner
        )
        try:
            promotion_plan_summary = planner_fn(
                list(target_tickers_tuple),
                staged_dir=staged_path,
                production_stable_dir=prod_stable_path,
                intervals=list(interval_list),
            )
            promotion_plan_ready = bool(
                promotion_plan_summary.get(
                    "plan_ready", False,
                )
            )
        except Exception as exc:
            promotion_plan_summary = {
                "error": "promotion_planner_failed",
                "detail": str(exc),
            }
            promotion_plan_ready = False
        if promotion_plan_ready is False:
            _append_unique(
                issues, ISSUE_PROMOTION_PLAN_NOT_READY,
            )

    # ----------------------------------------------------- 5. promotion writer dry-run
    promotion_writer_dry_run_summary: Optional[dict[str, Any]] = None
    if promotion_plan_ready is True:
        writer_fn = (
            promotion_writer_callable
            or _default_promotion_writer
        )
        try:
            promotion_writer_dry_run_summary = writer_fn(
                list(target_tickers_tuple),
                staged_dir=staged_path,
                production_stable_dir=prod_stable_path,
                intervals=list(interval_list),
                execution_log=promotion_writer_execution_log,
            )
        except Exception as exc:
            promotion_writer_dry_run_summary = {
                "error": "promotion_writer_failed",
                "detail": str(exc),
            }

    # ----------------------------------------------------- 6. multi-window K chain
    adapter_summary: Optional[dict[str, Any]] = None
    payload_summary: Optional[dict[str, Any]] = None
    patch_planner_summary: Optional[dict[str, Any]] = None
    patch_writer_dry_run_summary: Optional[dict[str, Any]] = None
    # Phase 6I-32 amendment-1: downstream chain ALSO skipped
    # when staged_dir is unsafe. The chain reads from
    # staged_dir; running it against an unsafe staged_dir
    # could attempt to load production stable files under a
    # sandbox interpretation.
    if (
        run_downstream_chain
        and primary
        and not unsafe_staged_dir
    ):
        adapter_fn = (
            adapter_diagnostic_callable
            or _default_adapter_diagnostic
        )
        try:
            adapter_summary = adapter_fn(
                primary,
                stackbuilder_root=stack_path,
                signal_library_dir=staged_path,
                cache_dir=cache_path,
            )
        except Exception as exc:
            adapter_summary = {
                "error": "adapter_diagnostic_failed",
                "detail": str(exc),
            }
        if not bool(
            (adapter_summary or {}).get(
                "can_evaluate_full_60_cell_grid", False,
            )
        ):
            _append_unique(issues, ISSUE_ADAPTER_NOT_FULL_GRID)

        payload_fn = (
            payload_builder_callable
            or _default_payload_builder
        )
        try:
            payload_summary = payload_fn(
                primary,
                stackbuilder_root=stack_path,
                signal_library_dir=staged_path,
                cache_dir=cache_path,
            )
        except Exception as exc:
            payload_summary = {
                "error": "payload_builder_failed",
                "detail": str(exc),
            }
        if not bool(
            (payload_summary or {}).get("payload_ready", False)
        ):
            _append_unique(issues, ISSUE_PAYLOAD_NOT_READY)

        patch_planner_fn = (
            patch_planner_callable
            or _default_patch_planner
        )
        try:
            patch_planner_summary = patch_planner_fn(
                primary,
                artifact_root=artifact_path,
                stackbuilder_root=stack_path,
                signal_library_dir=staged_path,
                cache_dir=cache_path,
                current_as_of_date=current_as_of_date,
            )
        except Exception as exc:
            patch_planner_summary = {
                "error": "patch_planner_failed",
                "detail": str(exc),
            }
        if not bool(
            (patch_planner_summary or {}).get(
                "patch_ready", False,
            )
        ):
            _append_unique(issues, ISSUE_PATCH_PLAN_NOT_READY)

        patch_writer_fn = (
            patch_writer_callable
            or _default_patch_writer
        )
        try:
            patch_writer_dry_run_summary = patch_writer_fn(
                primary,
                artifact_root=artifact_path,
                stackbuilder_root=stack_path,
                signal_library_dir=staged_path,
                cache_dir=cache_path,
                current_as_of_date=current_as_of_date,
                execution_log=patch_writer_execution_log,
            )
        except Exception as exc:
            patch_writer_dry_run_summary = {
                "error": "patch_writer_failed",
                "detail": str(exc),
            }

    # ----------------------------------------------------- 7. snapshot after + diff
    snapshot_after: Optional[dict[str, Any]] = None
    production_root_diff: Optional[dict[str, Any]] = None
    if run_snapshot_diff:
        try:
            snapshot_after = snapshot_helper()
        except Exception as exc:
            snapshot_after = {
                "error": "snapshot_helper_failed",
                "detail": str(exc),
            }
        diff_fn = (
            production_diff_callable
            or _default_production_diff
        )
        try:
            production_root_diff = diff_fn(
                snapshot_before, snapshot_after,
            )
        except Exception as exc:
            production_root_diff = {
                "error": "production_diff_failed",
                "detail": str(exc),
            }
        diff_total = (
            (production_root_diff or {})
            .get("TOTAL", {}) or {}
        )
        if (
            diff_total.get("added", 0)
            + diff_total.get("removed", 0)
            + diff_total.get("changed", 0)
            > 0
        ):
            _append_unique(
                issues,
                ISSUE_PRODUCTION_ROOT_DRIFT_DETECTED,
            )

    # ----------------------------------------------------- 8. classify state
    state = STATE_STAGED_REBUILD_READY
    if ISSUE_SOURCE_CACHE_NOT_READY in issues:
        state = STATE_SOURCE_NOT_READY
    elif (
        ISSUE_SANDBOX_BUILD_INCOMPLETE in issues
        or ISSUE_PROMOTION_PLAN_NOT_READY in issues
        or ISSUE_ADAPTER_NOT_FULL_GRID in issues
        or ISSUE_PAYLOAD_NOT_READY in issues
        or ISSUE_PATCH_PLAN_NOT_READY in issues
        or ISSUE_PRODUCTION_ROOT_DRIFT_DETECTED in issues
        or ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE in issues
    ):
        state = STATE_STAGED_REBUILD_NOT_READY

    if state == STATE_STAGED_REBUILD_READY:
        recommended = (
            "review_evidence_and_authorize_promotion_separately"
        )
    elif state == STATE_SOURCE_NOT_READY:
        recommended = "refresh_source_cache"
    else:
        recommended = "resolve_staged_rebuild_blocker"

    return FreshStagingReadinessReport(
        generated_at=_iso_now(),
        state=state,
        issue_codes=tuple(issues),
        target_tickers=target_tickers_tuple,
        intervals=interval_list,
        staged_dir=str(staged_path),
        production_stable_dir=str(prod_stable_path),
        confluence_artifact_root=str(artifact_path),
        source_cache_ready=source_cache_ready,
        cache_cutoff_summary=cache_cutoff_summary,
        source_availability_summary=(
            source_availability_summary
        ),
        sandbox_build_attempted=sandbox_attempted,
        sandbox_build_written=sandbox_written,
        sandbox_build_failed=sandbox_failed,
        promotion_plan_summary=promotion_plan_summary,
        promotion_plan_ready=promotion_plan_ready,
        promotion_writer_dry_run_summary=(
            promotion_writer_dry_run_summary
        ),
        adapter_diagnostic_summary=adapter_summary,
        payload_builder_summary=payload_summary,
        patch_planner_summary=patch_planner_summary,
        patch_writer_dry_run_summary=(
            patch_writer_dry_run_summary
        ),
        production_snapshot_before=snapshot_before,
        production_snapshot_after=snapshot_after,
        production_root_diff=production_root_diff,
        recommended_next_action=recommended,
    )


# ---------------------------------------------------------------------------
# Default production-root snapshot / diff helpers
# ---------------------------------------------------------------------------


_PRODUCTION_ROOTS_RELATIVE: tuple[str, ...] = (
    "cache/results",
    "cache/status",
    "output/research_artifacts",
    "output/stackbuilder",
    "signal_library/data/stable",
)


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_production_snapshot() -> dict[str, Any]:
    """Produce a relative_path_size_mtime snapshot for the
    five production roots. Read-only. Returns
    ``{"root_name": {"relative_path": (size, mtime), ...},
    "file_counts": {root_name: count}, "total_files": int}``.
    """
    project_root = _project_dir()
    out: dict[str, Any] = {
        "generated_at": _iso_now(),
        "roots": {},
        "file_counts": {},
        "total_files": 0,
    }
    total = 0
    for rel in _PRODUCTION_ROOTS_RELATIVE:
        root = project_root / rel
        files: dict[str, tuple[int, float]] = {}
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    try:
                        st = p.stat()
                        files[
                            str(p.relative_to(root))
                        ] = (st.st_size, st.st_mtime)
                    except Exception:
                        pass
        out["roots"][rel] = files
        out["file_counts"][rel] = len(files)
        total += len(files)
    out["total_files"] = total
    return out


def _default_production_diff(
    before: Optional[dict[str, Any]],
    after: Optional[dict[str, Any]],
) -> dict[str, Any]:
    if (
        not isinstance(before, Mapping)
        or not isinstance(after, Mapping)
    ):
        return {"error": "diff_inputs_invalid"}
    result: dict[str, Any] = {}
    total = {"added": 0, "removed": 0, "changed": 0}
    before_roots = before.get("roots", {}) or {}
    after_roots = after.get("roots", {}) or {}
    for rel in _PRODUCTION_ROOTS_RELATIVE:
        b = before_roots.get(rel, {}) or {}
        a = after_roots.get(rel, {}) or {}
        added = sum(1 for k in a if k not in b)
        removed = sum(1 for k in b if k not in a)
        changed = sum(
            1 for k in a
            if k in b and tuple(a[k]) != tuple(b[k])
        )
        result[rel] = {
            "added": added,
            "removed": removed,
            "changed": changed,
        }
        total["added"] += added
        total["removed"] += removed
        total["changed"] += changed
    result["TOTAL"] = total
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signal_library_fresh_staging_readiness",
        description=(
            "Phase 6I-32 supervised fresh-source readiness + "
            "staged signal-library rebuild evidence harness. "
            "STRICTLY READ-ONLY. Default callables delegate "
            "to the existing project modules; this harness "
            "never authorizes any --write path."
        ),
    )
    parser.add_argument(
        "--tickers", required=True,
        help="Comma-separated tickers (target universe).",
    )
    parser.add_argument(
        "--primary-ticker", default=None,
        help=(
            "Ticker to run the downstream multi-window K "
            "chain against. Default: first ticker."
        ),
    )
    parser.add_argument(
        "--staged-dir", required=True,
        help=(
            "Path to the staged sandbox output directory. "
            "MUST NOT be under signal_library/data/stable."
        ),
    )
    parser.add_argument(
        "--cache-dir", default="cache/results",
        help="Spymaster cache results dir. Default: cache/results.",
    )
    parser.add_argument(
        "--stackbuilder-root", default="output/stackbuilder",
        help="StackBuilder root. Default: output/stackbuilder.",
    )
    parser.add_argument(
        "--production-stable-dir",
        default=str(
            _project_dir() / "signal_library" / "data" / "stable",
        ),
        help=(
            "Production stable signal-library dir. Default: "
            "<project>/signal_library/data/stable."
        ),
    )
    parser.add_argument(
        "--confluence-artifact-root",
        default="output/research_artifacts",
        help=(
            "Confluence artifact root. Default: "
            "output/research_artifacts."
        ),
    )
    parser.add_argument(
        "--current-as-of-date", default=None,
    )
    parser.add_argument(
        "--intervals",
        default=",".join(DEFAULT_INTERVALS),
    )
    parser.add_argument(
        "--sandbox-end-date", default=None,
        help=(
            "Optional --end-date for the sandbox builder so a "
            "heterogeneous production-cache state can produce "
            "a common-cutoff sandbox snapshot."
        ),
    )
    parser.add_argument(
        "--skip-source-availability",
        action="store_true",
        help=(
            "Skip the yfinance-backed source-availability "
            "probe (fast mode; cache-cutoff probe still runs)."
        ),
    )
    parser.add_argument(
        "--skip-downstream-chain",
        action="store_true",
    )
    parser.add_argument(
        "--skip-snapshot-diff",
        action="store_true",
    )
    parser.add_argument(
        "--promotion-writer-execution-log",
        default=None,
    )
    parser.add_argument(
        "--patch-writer-execution-log",
        default=None,
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
    intervals = [
        i.strip() for i in args.intervals.split(",")
        if i.strip()
    ]
    if not tickers:
        print(
            json.dumps({"error": "missing_tickers"}),
            file=sys.stderr,
        )
        return 2

    try:
        report = evaluate_fresh_staging_readiness(
            tickers,
            staged_dir=args.staged_dir,
            cache_dir=args.cache_dir,
            stackbuilder_root=args.stackbuilder_root,
            production_stable_dir=args.production_stable_dir,
            confluence_artifact_root=(
                args.confluence_artifact_root
            ),
            current_as_of_date=args.current_as_of_date,
            intervals=intervals,
            sandbox_end_date=args.sandbox_end_date,
            primary_target_ticker=args.primary_ticker,
            promotion_writer_execution_log=(
                args.promotion_writer_execution_log
            ),
            patch_writer_execution_log=(
                args.patch_writer_execution_log
            ),
            run_source_availability=(
                not args.skip_source_availability
            ),
            run_downstream_chain=(
                not args.skip_downstream_chain
            ),
            run_snapshot_diff=(
                not args.skip_snapshot_diff
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

    print(json.dumps(report.to_json_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
