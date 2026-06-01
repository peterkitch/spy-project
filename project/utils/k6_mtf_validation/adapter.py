"""K=6 MTF validation adapter (SelectionAdapter for validation_engine).

Implements the K=6 MTF SelectionAdapter specified by
``project/md_library/shared/2026-05-31_K6_MTF_VALIDATION_PRODUCER_ADAPTER_SPEC.md``
and the methodology binding at
``project/md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md``
Section 13.5 (added by the 2026-05-31 amendment).

Strict input boundary:

- The adapter MUST NOT open ``output/k6_mtf/<run>/k6_mtf_history.json``
  or ``output/k6_mtf/<run>/k6_mtf_ranking.json`` as validation evidence.
  Those paths, when carried in ``StrategyCandidate.app_payload``, are
  provenance / audit metadata only.
- All per-fold evaluation reads from cutoff-safe upstream inputs:
  the upstream StackBuilder ``selected_build.json`` / ``combo_k=6.json``
  (the K=6 stack is frozen by the upstream selected build), the
  per-(member, timeframe) signal libraries sliced to
  ``ctx.train_end`` / ``ctx.evaluation_cutoff`` as appropriate, and
  the secondary's daily close sliced to ``ctx.evaluation_cutoff``.

No-lookahead per-fold flow:

- ``select_for_fold(ctx)`` returns one ``StrategyCandidate`` per
  launch-family secondary. The per-fold ``current_snapshot`` is
  synthesized at ``ctx.train_end`` (NOT from a live K=6 MTF artifact),
  using member signal libraries sliced to ``ctx.train_end`` and the
  same combine + forward-fill helpers the production producer uses.
- ``evaluate_candidate(candidate, ctx)`` walks the OOS window
  ``(ctx.test_start, ctx.test_end]`` over the secondary's close
  sliced to ``ctx.evaluation_cutoff``, applies the match-rule
  wildcard pass against the per-fold ``current_snapshot``, computes
  per-bar capture from the next close (close-pair must lie within
  ``ctx.evaluation_cutoff``), and emits a ``StrategyFoldResult`` whose
  ``daily_capture`` preserves no-trade ``0.0`` bars and whose
  ``trigger_mask`` is true only for matched bars whose own 1d
  direction is BUY or SHORT.
- ``baseline_for_fold(ctx)`` returns same-secondary buy-and-hold over
  the fold OOS window (locked 5C-1 Section 6), mirroring the
  StackBuilder pattern at ``stackbuilder.py:3618``.

Sidecar destination: ``output/validation/<run_id>/validation.json``
under ``<REPO_ROOT>``. The output-base resolver below anchors
``Path("project/output/validation")`` (the
``validation_engine.VALIDATION_OUTPUT_BASE_DIR`` repo-root-relative
default) under ``<REPO_ROOT>`` regardless of cwd, so an invocation
from ``<PROJECT_DIR>`` does NOT produce a doubled
``<PROJECT_DIR>/project/output/validation`` path.

Reuses ``k6_mtf_history_producer.apply_protocol(signal, protocol)``
verbatim (no duplication).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple,
)


# ---------------------------------------------------------------------------
# Project-root bootstrap so this module can be invoked as
# ``python -m utils.k6_mtf_validation.adapter`` from <PROJECT_DIR>.
# This file lives at <REPO_ROOT>/project/utils/k6_mtf_validation/adapter.py;
# <PROJECT_DIR> is the third parent of __file__.
# ---------------------------------------------------------------------------

_THIS_FILE = Path(__file__).resolve()
_PROJECT_DIR_DEFAULT = _THIS_FILE.parent.parent.parent
_REPO_ROOT_DEFAULT = _PROJECT_DIR_DEFAULT.parent

if str(_PROJECT_DIR_DEFAULT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR_DEFAULT))


# ---------------------------------------------------------------------------
# Project module imports (after bootstrap).
# ---------------------------------------------------------------------------

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from validation_engine import (  # noqa: E402
    BaselineFoldMetrics,
    DEFAULT_ALPHA,
    DEFAULT_BORDERLINE_TOLERANCE_MULTIPLIER,
    DEFAULT_INITIAL_TRAIN_DAYS,
    DEFAULT_N_BOOTSTRAP_SAMPLES,
    DEFAULT_N_PERMUTATIONS,
    DEFAULT_STEP_DAYS,
    DEFAULT_TEST_WINDOW_DAYS,
    FoldContext,
    StrategyCandidate,
    StrategyFoldResult,
    VALIDATION_CONTRACT_VERSION,
    VALIDATION_METHODOLOGY_VERSION,
    VALIDATION_OUTPUT_BASE_DIR,
    compute_validation_artifact_hash,
    generate_run_id,
    slice_between,
    slice_to_cutoff,
    validate_strategy_set,
    write_validation_sidecar,
)
from canonical_scoring import score_captures as _canonical_score_captures  # noqa: E402

import k6_mtf_history_producer as k6hp  # noqa: E402
from k6_mtf_history_producer import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    DEFAULT_PRICE_CACHE_DIR,
    DEFAULT_STABLE_DIR,
    DEFAULT_STACKBUILDER_ROOT,
    K6Stack,
    K6StackResolutionError,
    MemberLibraryError,
    NON_DAILY_TIMEFRAMES,
    SCHEMA_VERSION as HISTORY_SCHEMA_VERSION,
    SIGNAL_BUY,
    SIGNAL_NONE,
    SIGNAL_SHORT,
    SIGNAL_UNAVAILABLE,
    SecondarySourceError,
    TIMEFRAME_SET,
    _build_combined_series_for_timeframe,
    _build_one_d_slot_for_calendar,
    _forward_fill_combined_stream,
    apply_protocol,
    combine_six,
    load_member_library,
    load_secondary_close,
    resolve_k6_stack,
)
from k6_mtf_ranking_engine import (  # noqa: E402
    LOW_SAMPLE_THRESHOLD,
    TIMEFRAMES,
    TRADE_DIRECTION_BUY,
    TRADE_DIRECTION_NONE,
    TRADE_DIRECTION_SHORT,
    WILDCARD_SIGNAL_VALUES,
    _bar_matches_alignment,
    _candidate_trade_direction,
    _normalize_snapshot,
    _safe_positive_close,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

K6_MTF_PRODUCER_ENGINE = "k6_mtf"
K6_MTF_APP_SURFACE = "run_directory"
K6_MTF_REASON_PREFIX = "K6MTF"

REASON_MISSING_SELECTED_BUILD = "missing_selected_build"
REASON_MISSING_COMBO_K6 = "missing_combo_k6"
REASON_MISSING_MEMBER_LIBRARY = "missing_member_library"
REASON_MISSING_SECONDARY_CLOSE = "missing_secondary_close"
REASON_HISTORY_UNDERFLOW = "history_underflow"
REASON_NO_TRIGGERS = "no_triggers"
REASON_STDDEV_ZERO = "stddev_zero"

_DEFAULT_LAUNCH_FAMILY: Tuple[str, ...] = (
    "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "SPY", "TSLA",
)


# ---------------------------------------------------------------------------
# Output-base resolver (path-doubling guard)
# ---------------------------------------------------------------------------


def resolve_validation_output_base(
    *,
    project_dir: Optional[Path] = None,
    repo_root: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> Path:
    """Resolve ``validation_engine.VALIDATION_OUTPUT_BASE_DIR`` to an
    absolute path safely from either ``<PROJECT_DIR>`` or
    ``<REPO_ROOT>`` cwd.

    ``VALIDATION_OUTPUT_BASE_DIR`` is the literal repo-root-relative
    ``Path("project/output/validation")``. Naively joining
    ``<PROJECT_DIR>`` (= ``<REPO_ROOT>/project``) with this base
    produces ``<REPO_ROOT>/project/project/output/validation``. The
    rule applied here:

    - If ``base_dir`` is absolute, return it.
    - If ``base_dir`` parts start with ``"project"``, anchor under
      ``repo_root`` so ``<REPO_ROOT>/project/output/validation`` is
      produced regardless of cwd.
    - Otherwise anchor under ``project_dir``.

    Defaults: ``project_dir = <REPO_ROOT>/project`` derived from
    ``Path(__file__)``; ``repo_root = project_dir.parent``;
    ``base_dir = VALIDATION_OUTPUT_BASE_DIR``.
    """
    if base_dir is None:
        base_dir = VALIDATION_OUTPUT_BASE_DIR
    if project_dir is None:
        project_dir = _PROJECT_DIR_DEFAULT
    if repo_root is None:
        repo_root = Path(project_dir).parent
    base = Path(base_dir)
    if base.is_absolute():
        return base.resolve()
    parts = base.parts
    if parts and parts[0] == "project":
        return (Path(repo_root) / base).resolve()
    return (Path(project_dir) / base).resolve()


# ---------------------------------------------------------------------------
# Reason-code formatting (locked 5C-1 Section15 prefix + adapter-specific codes)
# ---------------------------------------------------------------------------


def _format_reason(code: str, message: str) -> str:
    """Build a single bracketed ``[K6MTF:<code>] <message>`` reason
    string suitable for ``StrategyFoldResult.issues``.
    """
    return f"[{K6_MTF_REASON_PREFIX}:{code}] {message}"


# ---------------------------------------------------------------------------
# Cutoff-safe per-fold history synthesis
# ---------------------------------------------------------------------------


def _slice_member_library(
    lib: Mapping[str, Any], cutoff: pd.Timestamp,
) -> dict:
    """Return a copy of ``lib`` with ``dates`` and ``signals`` sliced
    to ``[start, cutoff]``. ``lib`` carries ``dates`` (DatetimeIndex)
    and ``signals`` (list) per ``load_member_library``.
    """
    idx = pd.DatetimeIndex(lib["dates"])
    sigs = list(lib["signals"])
    mask = idx <= pd.Timestamp(cutoff)
    new_idx = idx[mask]
    new_sigs = [s for s, keep in zip(sigs, mask) if keep]
    out = dict(lib)
    out["dates"] = new_idx
    out["signals"] = new_sigs
    return out


def _synthesize_current_snapshot(
    stack: K6Stack,
    member_libs_by_tf: Mapping[str, Dict[str, dict]],
    secondary_close: pd.Series,
    cutoff: pd.Timestamp,
) -> Optional[Dict[str, str]]:
    """Synthesize the per-fold ``current_snapshot`` at ``cutoff``.

    Mirrors the production producer's per-bar snapshot assembly
    (combine + forward-fill for non-daily timeframes; exact-date
    member 1d slot for the 1d timeframe) but substitutes ``cutoff``
    for the live producer's ``bars[-1]`` date.

    Returns ``None`` if ``cutoff`` is before the first available
    secondary close bar (the fold has no in-sample secondary bar to
    anchor the snapshot against).
    """
    sec_capped = secondary_close[secondary_close.index <= cutoff]
    if sec_capped.empty:
        return None
    target_calendar = pd.DatetimeIndex([sec_capped.index[-1]])

    member_libs_capped_by_tf: Dict[str, Dict[str, dict]] = {}
    for tf in TIMEFRAME_SET:
        per_tf: Dict[str, dict] = {}
        for member in stack.members:
            lib = member_libs_by_tf[tf][member.ticker]
            per_tf[member.ticker] = _slice_member_library(lib, cutoff)
        member_libs_capped_by_tf[tf] = per_tf

    one_d_blocks = _build_one_d_slot_for_calendar(
        stack, member_libs_capped_by_tf["1d"], target_calendar,
    )
    snapshot: Dict[str, str] = {"1d": one_d_blocks[0]["signal"]}
    for tf in NON_DAILY_TIMEFRAMES:
        union_idx, combined_sigs, counts = (
            _build_combined_series_for_timeframe(
                tf, stack, member_libs_capped_by_tf,
            )
        )
        if len(union_idx):
            mask = union_idx <= cutoff
            union_idx = union_idx[mask]
            combined_sigs = [
                s for s, keep in zip(combined_sigs, mask) if keep
            ]
            counts = [c for c, keep in zip(counts, mask) if keep]
        block = _forward_fill_combined_stream(
            union_idx, combined_sigs, counts, target_calendar,
        )[0]
        snapshot[tf] = block["signal"]
    return _normalize_snapshot(snapshot)


def _synthesize_candidate_snapshots_in_window(
    stack: K6Stack,
    member_libs_by_tf: Mapping[str, Dict[str, dict]],
    secondary_close: pd.Series,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    *,
    evaluation_cutoff: pd.Timestamp,
) -> List[Dict[str, Any]]:
    """Synthesize the per-bar K=6 MTF snapshots for OOS candidate
    bars in ``[window_start, window_end]``.

    Source data is sliced to ``evaluation_cutoff`` so per-target
    forward-fill uses only source dates ``<= target`` AND
    ``<= evaluation_cutoff``. The secondary close window also
    respects ``evaluation_cutoff`` so the "next close" used by
    per-bar capture cannot land past the cutoff.
    """
    sec_capped = secondary_close[secondary_close.index <= evaluation_cutoff]
    target_calendar = sec_capped.index[
        (sec_capped.index >= window_start) & (sec_capped.index <= window_end)
    ]
    if len(target_calendar) == 0:
        return []

    member_libs_capped_by_tf: Dict[str, Dict[str, dict]] = {}
    for tf in TIMEFRAME_SET:
        per_tf: Dict[str, dict] = {}
        for member in stack.members:
            lib = member_libs_by_tf[tf][member.ticker]
            per_tf[member.ticker] = _slice_member_library(
                lib, evaluation_cutoff,
            )
        member_libs_capped_by_tf[tf] = per_tf

    one_d_blocks = _build_one_d_slot_for_calendar(
        stack, member_libs_capped_by_tf["1d"], target_calendar,
    )
    non_daily_blocks: Dict[str, List[Dict[str, Any]]] = {}
    for tf in NON_DAILY_TIMEFRAMES:
        union_idx, combined_sigs, counts = (
            _build_combined_series_for_timeframe(
                tf, stack, member_libs_capped_by_tf,
            )
        )
        if len(union_idx):
            mask = union_idx <= evaluation_cutoff
            union_idx = union_idx[mask]
            combined_sigs = [
                s for s, keep in zip(combined_sigs, mask) if keep
            ]
            counts = [c for c, keep in zip(counts, mask) if keep]
        non_daily_blocks[tf] = _forward_fill_combined_stream(
            union_idx, combined_sigs, counts, target_calendar,
        )

    bars: List[Dict[str, Any]] = []
    for i, bar_date in enumerate(target_calendar):
        snapshot: Dict[str, str] = {"1d": one_d_blocks[i]["signal"]}
        for tf in NON_DAILY_TIMEFRAMES:
            snapshot[tf] = non_daily_blocks[tf][i]["signal"]
        bars.append({
            "date_utc": pd.Timestamp(bar_date).strftime("%Y-%m-%d"),
            "bar_date": pd.Timestamp(bar_date),
            "snapshot": _normalize_snapshot(snapshot),
        })
    return bars


# ---------------------------------------------------------------------------
# Per-secondary upstream input bundle
# ---------------------------------------------------------------------------


@dataclass
class _SecondaryInputs:
    """Per-secondary cutoff-safe upstream input bundle.

    Holds the resolved K=6 stack, per-(member, timeframe) signal
    libraries, and secondary daily close series. ``available`` is
    False when any required input is missing or unreadable; in that
    case ``issues`` carries one or more bracketed ``[K6MTF:...]``
    reason strings and the other fields are unset.
    """

    secondary: str
    available: bool
    stack: Optional[K6Stack] = None
    member_libs_by_tf: Optional[Dict[str, Dict[str, dict]]] = None
    secondary_close: Optional[pd.Series] = None
    secondary_close_path: Optional[str] = None
    secondary_close_kind: Optional[str] = None
    issues: Tuple[str, ...] = ()


def _load_secondary_inputs(
    secondary: str,
    *,
    stackbuilder_root: str,
    stable_dir: str,
    price_cache_dir: str,
    cache_dir: str,
) -> _SecondaryInputs:
    """Load all cutoff-safe upstream inputs for one secondary."""
    issues: List[str] = []
    try:
        stack = resolve_k6_stack(
            secondary, stackbuilder_root=stackbuilder_root,
        )
    except K6StackResolutionError as exc:
        msg = str(exc)
        if "selected_build.json" in msg:
            code = REASON_MISSING_SELECTED_BUILD
        elif "combo_k=6.json" in msg or "K=6 members" in msg:
            code = REASON_MISSING_COMBO_K6
        else:
            code = REASON_MISSING_SELECTED_BUILD
        issues.append(_format_reason(code, f"{secondary}: {exc}"))
        return _SecondaryInputs(
            secondary=secondary, available=False, issues=tuple(issues),
        )

    member_libs_by_tf: Dict[str, Dict[str, dict]] = {
        tf: {} for tf in TIMEFRAME_SET
    }
    for member in stack.members:
        for tf in TIMEFRAME_SET:
            try:
                lib = load_member_library(
                    member.ticker, tf, stable_dir=stable_dir,
                )
            except MemberLibraryError as exc:
                issues.append(_format_reason(
                    REASON_MISSING_MEMBER_LIBRARY,
                    f"{secondary}: member {member.ticker} {tf}: {exc}",
                ))
                return _SecondaryInputs(
                    secondary=secondary, available=False,
                    issues=tuple(issues),
                )
            member_libs_by_tf[tf][member.ticker] = lib

    try:
        sec_series, sec_path, sec_kind = load_secondary_close(
            secondary,
            price_cache_dir=price_cache_dir,
            cache_dir=cache_dir,
        )
    except SecondarySourceError as exc:
        issues.append(_format_reason(
            REASON_MISSING_SECONDARY_CLOSE,
            f"{secondary}: {exc}",
        ))
        return _SecondaryInputs(
            secondary=secondary, available=False, issues=tuple(issues),
        )

    return _SecondaryInputs(
        secondary=secondary,
        available=True,
        stack=stack,
        member_libs_by_tf=member_libs_by_tf,
        secondary_close=sec_series,
        secondary_close_path=sec_path,
        secondary_close_kind=sec_kind,
    )


# ---------------------------------------------------------------------------
# SelectionAdapter implementation
# ---------------------------------------------------------------------------


class K6MtfValidationAdapter:
    """SelectionAdapter for K=6 MTF validation.

    Returns one ``StrategyCandidate`` per launch-family secondary.
    Per-fold ``current_snapshot`` is synthesized from cutoff-safe
    upstream inputs at ``ctx.train_end``; OOS candidate bars are
    synthesized from upstream inputs through ``ctx.evaluation_cutoff``.
    Reads zero bytes from ``output/k6_mtf/<run>/**``.
    """

    def __init__(
        self,
        *,
        secondaries: Sequence[str],
        secondary_inputs: Mapping[str, _SecondaryInputs],
    ) -> None:
        self.secondaries = tuple(
            str(s).strip().upper() for s in secondaries if str(s).strip()
        )
        self._inputs: Dict[str, _SecondaryInputs] = {
            str(k).strip().upper(): v
            for k, v in secondary_inputs.items()
        }
        # Per-fold synthesized current_snapshot cache, keyed by
        # (fold_index, secondary). Used by evaluate_candidate so the
        # candidate's app_payload current_snapshot field never has to
        # be trusted: the cache holds the value synthesized inside
        # select_for_fold from cutoff-safe inputs.
        self._fold_current_snapshot: Dict[
            Tuple[int, str], Optional[Dict[str, str]]
        ] = {}
        # Per-(strategy_id, fold_index) same-secondary baseline cache,
        # populated by evaluate_candidate on every return path. Read by
        # run_validation() after validate_strategy_set returns, before
        # write_validation_sidecar, so each
        # strategies[].per_fold_metrics[] entry can carry a persisted
        # same_secondary_baseline field in the JSON sidecar.
        # StrategyFoldResult.metadata is NOT serialized into the
        # contract by validation_engine v1 (_merge_strategy_metadata
        # keeps only empirical-layer keys), so transient-only metadata
        # would lose the per-strategy baseline evidence on persistence.
        self._fold_baseline_cache: Dict[
            Tuple[str, int], Dict[str, Any]
        ] = {}

    # ----- SelectionAdapter Protocol --------------------------------------

    def history_index(self) -> pd.DatetimeIndex:
        """Union of available-secondary close calendars (sorted, unique).

        Used by ``compute_walk_forward_folds`` to size the fold grid.
        Secondaries whose inputs are unavailable contribute no dates.
        """
        union = pd.DatetimeIndex([])
        for sec in self.secondaries:
            inputs = self._inputs.get(sec)
            if inputs is None or not inputs.available:
                continue
            close = inputs.secondary_close
            if close is None or close.empty:
                continue
            union = union.union(close.index)
        return pd.DatetimeIndex(sorted(union.unique()))

    def select_for_fold(
        self, context: FoldContext,
    ) -> Sequence[StrategyCandidate]:
        """One ``StrategyCandidate`` per launch-family secondary.

        Missing-input secondaries remain visible as candidates with
        ``app_payload['input_available'] = False`` and bracketed
        ``[K6MTF:...]`` issues so ``n_strategies_tested`` reflects the
        input family size.
        """
        candidates: List[StrategyCandidate] = []
        for sec in self.secondaries:
            inputs = self._inputs.get(sec)
            history_as_of = pd.Timestamp(context.train_end).strftime(
                "%Y-%m-%d",
            )
            # strategy_id is stable across folds so validate_strategy_set
            # aggregates per-secondary fold results under a single
            # strategy. The per-fold cutoff goes in app_payload and the
            # strategy_label as provenance only.
            sid = f"k6_mtf:{sec}"
            label = f"K=6 MTF {sec} fold_as_of={history_as_of}"

            if inputs is None or not inputs.available:
                candidates.append(StrategyCandidate(
                    strategy_id=sid,
                    strategy_label=label,
                    app_payload={
                        "secondary": sec,
                        "input_available": False,
                        "history_as_of_date": history_as_of,
                        "issues": list(
                            inputs.issues if inputs else (
                                _format_reason(
                                    REASON_MISSING_SELECTED_BUILD,
                                    f"{sec}: secondary inputs not loaded",
                                ),
                            )
                        ),
                    },
                ))
                self._fold_current_snapshot[
                    (context.fold_index, sec)
                ] = None
                continue

            current_snapshot = _synthesize_current_snapshot(
                inputs.stack,
                inputs.member_libs_by_tf,
                inputs.secondary_close,
                pd.Timestamp(context.train_end),
            )
            self._fold_current_snapshot[
                (context.fold_index, sec)
            ] = current_snapshot

            input_underflow = current_snapshot is None
            issues_for_candidate: List[str] = []
            if input_underflow:
                issues_for_candidate.append(_format_reason(
                    REASON_HISTORY_UNDERFLOW,
                    (
                        f"{sec}: no secondary close bar at or before "
                        f"selection_cutoff={history_as_of}"
                    ),
                ))

            stack_members = [
                {"ticker": m.ticker, "protocol": m.protocol}
                for m in inputs.stack.members
            ]
            candidates.append(StrategyCandidate(
                strategy_id=sid,
                strategy_label=label,
                app_payload={
                    "secondary": sec,
                    "input_available": True,
                    "history_as_of_date": history_as_of,
                    "k6_stack_members": stack_members,
                    "selected_build_path": inputs.stack.selected_build_path,
                    "selected_run_dir": inputs.stack.selected_run_dir,
                    "combo_k6_path": inputs.stack.combo_k6_path,
                    "current_snapshot": (
                        dict(current_snapshot)
                        if current_snapshot is not None else None
                    ),
                    "secondary_close_path": inputs.secondary_close_path,
                    "secondary_close_kind": inputs.secondary_close_kind,
                    "issues": issues_for_candidate,
                },
            ))
        return candidates

    def evaluate_candidate(
        self,
        candidate: StrategyCandidate,
        context: FoldContext,
    ) -> StrategyFoldResult:
        """Walk the OOS window and emit ``daily_capture`` /
        ``trigger_mask`` per the K=6 MTF launch-path contract.
        """
        payload = candidate.app_payload or {}
        sec = str(payload.get("secondary") or "").strip().upper()
        inputs = self._inputs.get(sec)
        oos_test_start = pd.Timestamp(context.test_start)
        oos_test_end = pd.Timestamp(context.test_end)
        eval_cutoff = pd.Timestamp(context.evaluation_cutoff)

        if inputs is None or not inputs.available:
            issues_seq: Sequence[str] = (
                inputs.issues if inputs
                else (
                    _format_reason(
                        REASON_MISSING_SELECTED_BUILD,
                        f"{sec}: secondary inputs not loaded",
                    ),
                )
            )
            empty_baseline = _empty_per_strategy_baseline()
            self._fold_baseline_cache[
                (candidate.strategy_id, context.fold_index)
            ] = empty_baseline
            return _empty_fold_result(
                context, candidate, issues_seq,
                metadata_extra={
                    "secondary": sec,
                    "same_secondary_baseline": empty_baseline,
                },
            )

        # Per-(strategy, fold) same-secondary buy-and-hold. Computed
        # once and threaded onto every return path so the engine's
        # fold-level baseline_for_fold (which is deliberately empty
        # for K=6 MTF) does not become the source of misleading
        # baseline deltas. Also cached on the adapter so run_validation
        # can post-process the contract dict before sidecar
        # persistence (validation_engine v1 does not serialize
        # StrategyFoldResult.metadata into the contract).
        same_secondary_baseline = _compute_same_secondary_baseline(
            inputs.secondary_close,
            test_start=oos_test_start,
            test_end=oos_test_end,
            evaluation_cutoff=eval_cutoff,
        )
        self._fold_baseline_cache[
            (candidate.strategy_id, context.fold_index)
        ] = same_secondary_baseline

        current_snapshot = self._fold_current_snapshot.get(
            (context.fold_index, sec),
        )
        if current_snapshot is None:
            return _empty_fold_result(
                context, candidate,
                (_format_reason(
                    REASON_HISTORY_UNDERFLOW,
                    f"{sec}: no current_snapshot for fold "
                    f"{context.fold_index}",
                ),),
                metadata_extra={
                    "secondary": sec,
                    "same_secondary_baseline": same_secondary_baseline,
                },
            )

        # Build the OOS candidate-bar synthesis using cutoff-safe
        # inputs. We need the secondary close at the test window plus
        # one bar past the last test day for the "next close" of the
        # final matched bar; secondary close is sliced to
        # evaluation_cutoff which equals test_end so the next-close
        # lookup naturally falls inside the cutoff.
        bars = _synthesize_candidate_snapshots_in_window(
            inputs.stack,
            inputs.member_libs_by_tf,
            inputs.secondary_close,
            window_start=oos_test_start,
            window_end=oos_test_end,
            evaluation_cutoff=eval_cutoff,
        )
        if not bars:
            return _empty_fold_result(
                context, candidate,
                (_format_reason(
                    REASON_NO_TRIGGERS,
                    f"{sec}: empty OOS window "
                    f"{oos_test_start.strftime('%Y-%m-%d')}..."
                    f"{oos_test_end.strftime('%Y-%m-%d')}",
                ),),
                metadata_extra={
                    "secondary": sec,
                    "same_secondary_baseline": same_secondary_baseline,
                },
            )

        sec_capped = inputs.secondary_close[
            inputs.secondary_close.index <= eval_cutoff
        ]
        close_lookup = sec_capped

        captures: List[float] = []
        trigger_flags: List[bool] = []
        capture_dates: List[pd.Timestamp] = []
        # Direction-preserving empirical-layer metadata mirrors the
        # StackBuilder precedent at stackbuilder.py:3608-3614:
        #   signal_state    -- full Buy/Short/None token series over
        #                      eligible OOS bars (matched + non-matched).
        #   raw_return_pool -- full raw unsigned (next_close/cur_close-1)
        #                      percent-return series over the same
        #                      eligible bars.
        # validation_engine._permutation_p_value counts literal "Buy"
        # and "Short" tokens, then samples n_buy+n_short raw returns
        # without replacement from the pool. The pool must therefore
        # span the FULL eligible OOS population (typically larger than
        # the trigger count) so the without-replacement sample succeeds
        # and the empirical null is the eligible-bar return distribution.
        empirical_signal_states: List[str] = []
        empirical_raw_returns: List[float] = []
        empirical_dates: List[pd.Timestamp] = []
        match_count = 0
        capture_count = 0
        trade_count = 0
        no_trade_count = 0
        skipped_capture_count = 0
        issues: List[str] = []

        # Precompute positional next-close lookups along the
        # evaluation-cutoff-bounded calendar so the "next close" cannot
        # fall past the cutoff.
        idx = close_lookup.index
        for bar in bars:
            bar_date = bar["bar_date"]
            cand_snapshot = bar["snapshot"]
            matched = _bar_matches_alignment(
                cand_snapshot, current_snapshot,
            )
            if matched:
                match_count += 1
            pos = idx.searchsorted(bar_date, side="right") - 1
            if pos < 0 or pos >= len(idx) - 1:
                # Bar is not eligible: no valid next close inside the
                # evaluation cutoff. Skipped-capture accounting is
                # matched-only per the K=6 MTF launch-path contract.
                if matched:
                    skipped_capture_count += 1
                continue
            cur_close = _safe_positive_close(close_lookup.iloc[pos])
            nxt_close = _safe_positive_close(close_lookup.iloc[pos + 1])
            if cur_close is None or nxt_close is None:
                if matched:
                    skipped_capture_count += 1
                continue
            raw_return_pct = (nxt_close / cur_close - 1.0) * 100.0
            # Eligible bar (valid current and next close inside
            # evaluation_cutoff): contribute one row to the empirical
            # pool. signal_state is "Buy"/"Short" only for matched
            # directional-trade bars; matched no-trade bars and every
            # non-matched bar carry signal_state="None". The pool
            # entry is ALWAYS the real raw next-close return,
            # regardless of trigger direction -- no synthetic 0.0 and
            # no NaN.
            empirical_dates.append(pd.Timestamp(bar_date))
            empirical_raw_returns.append(float(raw_return_pct))
            if matched:
                direction = _candidate_trade_direction(cand_snapshot)
                if direction == TRADE_DIRECTION_BUY:
                    cap = raw_return_pct
                    trade_count += 1
                    trigger_flags.append(True)
                    empirical_signal_states.append("Buy")
                elif direction == TRADE_DIRECTION_SHORT:
                    cap = -raw_return_pct
                    trade_count += 1
                    trigger_flags.append(True)
                    empirical_signal_states.append("Short")
                else:
                    cap = 0.0
                    no_trade_count += 1
                    trigger_flags.append(False)
                    empirical_signal_states.append("None")
                capture_count += 1
                captures.append(float(cap))
                capture_dates.append(pd.Timestamp(bar_date))
            else:
                # Non-matched eligible bar: pool participant only;
                # does not contribute to daily_capture / trigger_mask.
                empirical_signal_states.append("None")

        if capture_count == 0:
            issues.append(_format_reason(
                REASON_NO_TRIGGERS,
                f"{sec}: fold {context.fold_index} produced 0 captures "
                f"({match_count} matches, {skipped_capture_count} skipped)",
            ))
            return _empty_fold_result(
                context, candidate, tuple(issues),
                metadata_extra={
                    "secondary": sec,
                    "match_count": match_count,
                    "capture_count": capture_count,
                    "trade_count": trade_count,
                    "no_trade_count": no_trade_count,
                    "skipped_capture_count": skipped_capture_count,
                    "same_secondary_baseline": same_secondary_baseline,
                },
            )

        if trade_count == 0:
            issues.append(_format_reason(
                REASON_NO_TRIGGERS,
                f"{sec}: fold {context.fold_index} produced 0 directional "
                f"trades ({match_count} matches, {capture_count} captures, "
                f"{no_trade_count} no-trade)",
            ))

        daily_capture = pd.Series(
            captures, index=pd.DatetimeIndex(capture_dates), dtype=float,
        )
        trigger_mask = pd.Series(
            trigger_flags, index=daily_capture.index, dtype=bool,
        )
        empirical_index = pd.DatetimeIndex(empirical_dates)
        signal_state = pd.Series(
            empirical_signal_states, index=empirical_index, dtype=object,
        )
        permutation_return_pool = pd.Series(
            empirical_raw_returns, index=empirical_index, dtype=float,
        )

        return StrategyFoldResult(
            fold_index=context.fold_index,
            strategy_id=candidate.strategy_id,
            strategy_label=candidate.strategy_label,
            daily_capture=daily_capture,
            trigger_mask=trigger_mask,
            issues=tuple(issues),
            metadata={
                "secondary": sec,
                "current_snapshot": dict(current_snapshot),
                "match_count": match_count,
                "capture_count": capture_count,
                "trade_count": trade_count,
                "no_trade_count": no_trade_count,
                "skipped_capture_count": skipped_capture_count,
                "low_sample_warning": trade_count < LOW_SAMPLE_THRESHOLD,
                "same_secondary_baseline": same_secondary_baseline,
                "signal_state": signal_state,
                "permutation_return_pool": permutation_return_pool,
            },
        )

    def baseline_for_fold(
        self, context: FoldContext,
    ) -> BaselineFoldMetrics:
        """Return a deliberately empty fold-level baseline.

        ``validation_engine`` v1 carries exactly one
        ``BaselineFoldMetrics`` per fold via
        ``adapter.baseline_for_fold``; the engine then applies that
        single baseline to every per-strategy ``baseline_delta`` entry
        for that fold. For the K=6 MTF launch family (multiple
        secondaries evaluated under the same fold) no single
        fold-level baseline can honestly represent the locked 5C-1
        Section 6 same-secondary buy-and-hold contract -- a
        family-blended baseline would deliver misleading baseline
        deltas to every per-secondary strategy, and any per-secondary
        choice would be wrong for the other secondaries.

        Honest design: leave the fold-level baseline unavailable
        (``n_observations=0`` and all metric fields ``None``, with a
        bracketed ``[K6MTF:validation_baseline_unavailable]`` issue)
        so the engine's per-strategy ``per_fold_baseline_delta``
        entries surface as ``sharpe_delta=None``/``return_delta=None``
        rather than blended values. The actual same-secondary
        buy-and-hold metrics ARE computed inside
        ``evaluate_candidate`` and recorded on
        ``StrategyFoldResult.metadata['same_secondary_baseline']`` per
        ``(strategy, fold)`` so the evidence is preserved.

        A future ``validation_engine`` amendment that lets adapters
        emit per-``(strategy, fold)`` baselines would let the K=6
        MTF adapter pipe the same-secondary baseline through the
        contract's ``baseline_per_fold`` and per-strategy
        ``per_fold_baseline_delta`` fields. Until then the metadata
        path is the honest answer.
        """
        formatted = _format_reason(
            "validation_baseline_unavailable",
            (
                f"fold-{context.fold_index}: K=6 MTF launch family carries "
                f"per-secondary baselines; same-secondary buy-and-hold "
                f"metrics for each strategy are recorded on "
                f"StrategyFoldResult.metadata['same_secondary_baseline']"
            ),
        )
        return BaselineFoldMetrics(
            fold_index=context.fold_index,
            n_observations=0,
            baseline_sharpe=None,
            baseline_total_return=None,
            baseline_mean_return=None,
            baseline_std=None,
            issues=(formatted,),
        )


def _compute_same_secondary_baseline(
    secondary_close: pd.Series,
    *,
    test_start: pd.Timestamp,
    test_end: pd.Timestamp,
    evaluation_cutoff: pd.Timestamp,
) -> Dict[str, Optional[float]]:
    """Compute per-(strategy, fold) same-secondary buy-and-hold for
    the fold OOS window, bounded by ``evaluation_cutoff``.

    Returns a dict suitable for
    ``StrategyFoldResult.metadata['same_secondary_baseline']``. Honors
    the locked 5C-1 Section 6 same-secondary buy-and-hold contract
    per-strategy. Fields:

    - ``n_observations``: number of OOS daily bars used.
    - ``baseline_sharpe`` / ``baseline_total_return`` /
      ``baseline_mean_return`` / ``baseline_std``: floats or ``None``.
    - ``issues``: list of bracketed ``[K6MTF:...]`` reason strings
      (currently only populated on canonical-scoring exceptions).
    """
    out: Dict[str, Optional[float]] = {
        "n_observations": 0,
        "baseline_sharpe": None,
        "baseline_total_return": None,
        "baseline_mean_return": None,
        "baseline_std": None,
        "issues": [],
    }
    if secondary_close is None or secondary_close.empty:
        return out
    sec_capped = secondary_close[
        secondary_close.index <= pd.Timestamp(evaluation_cutoff)
    ]
    test_window = slice_between(sec_capped, test_start, test_end)
    if test_window is None or test_window.empty:
        return out
    prices = test_window.astype(float)
    daily_returns = prices.pct_change().fillna(0.0) * 100.0
    n_obs = int(len(daily_returns))
    out["n_observations"] = n_obs
    if n_obs == 0:
        return out
    all_true = pd.Series([True] * n_obs, index=daily_returns.index)
    try:
        score = _canonical_score_captures(
            daily_returns, all_true,
            risk_free_rate=5.0,
            periods_per_year=252,
            ddof=1,
        )
    except Exception as exc:
        out["issues"] = [_format_reason(
            "validation_baseline_unavailable",
            (
                f"per-strategy baseline scoring raised "
                f"{type(exc).__name__}: {exc}"
            ),
        )]
        return out
    out["baseline_sharpe"] = _safe_score_float(
        getattr(score, "sharpe", None),
    )
    out["baseline_total_return"] = _safe_score_float(
        getattr(score, "total_capture", None),
    )
    out["baseline_mean_return"] = _safe_score_float(
        getattr(score, "avg_daily_capture", None),
    )
    out["baseline_std"] = _safe_score_float(
        getattr(score, "std_dev", None),
    )
    return out


def _safe_score_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f


def _empty_per_strategy_baseline() -> Dict[str, Optional[float]]:
    """Placeholder same-secondary baseline used by empty-fold result
    paths (missing input, no history). Same shape as the success path
    so downstream readers can inspect a single stable schema.
    """
    return {
        "n_observations": 0,
        "baseline_sharpe": None,
        "baseline_total_return": None,
        "baseline_mean_return": None,
        "baseline_std": None,
        "issues": [],
    }


def _empty_fold_result(
    context: FoldContext,
    candidate: StrategyCandidate,
    issues: Sequence[str],
    *,
    metadata_extra: Optional[Mapping[str, Any]] = None,
) -> StrategyFoldResult:
    """Return an empty ``StrategyFoldResult`` carrying the issues
    bracketed-reason list and any extra metadata (count taxonomy,
    secondary identifier, etc.).
    """
    metadata: Dict[str, Any] = dict(metadata_extra or {})
    return StrategyFoldResult(
        fold_index=context.fold_index,
        strategy_id=candidate.strategy_id,
        strategy_label=candidate.strategy_label,
        daily_capture=pd.Series([], dtype=float),
        trigger_mask=pd.Series([], dtype=bool),
        issues=tuple(issues),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Contract post-processor (persists per-(strategy, fold) baseline)
# ---------------------------------------------------------------------------


def _enrich_contract_with_same_secondary_baseline(
    contract: Dict[str, Any],
    baseline_cache: Mapping[Tuple[str, int], Mapping[str, Any]],
) -> None:
    """Inject ``same_secondary_baseline`` into every
    ``strategies[].per_fold_metrics[]`` entry of an already-built
    ``validation_contract_v1`` dict, in place.

    ``validation_engine`` v1 does not serialize arbitrary
    ``StrategyFoldResult.metadata`` into the emitted contract; it
    only keeps engine-recognized fields on ``per_fold_metrics`` and
    only empirical-layer keys on ``_merge_strategy_metadata``. The
    K=6 MTF same-secondary buy-and-hold evidence (locked 5C-1
    Section 6) MUST live on disk in the sidecar, not just in the
    transient ``StrategyFoldResult.metadata``, so adapter-local
    post-processing fills the gap without requiring a
    ``validation_engine`` contract change.

    Lookup key: ``(strategy_id, fold_index)`` from each per-fold
    entry. Missing entries get ``_empty_per_strategy_baseline()`` so
    the per-fold metric schema is uniform across all strategies and
    folds. The injected dict shape:

    ``{"n_observations": int, "baseline_sharpe": Optional[float],
    "baseline_total_return": Optional[float],
    "baseline_mean_return": Optional[float],
    "baseline_std": Optional[float], "issues": List[str]}``.

    Engine-level ``per_fold_baseline_delta`` entries are left
    untouched (they remain ``sharpe_delta=None`` /
    ``return_delta=None`` because the adapter's ``baseline_for_fold``
    is deliberately empty). Downstream readers (honest_validation
    _ledger, future Phase 5 report tooling) should read the
    per-strategy ``same_secondary_baseline`` for K=6 MTF rows.
    """
    strategies = contract.get("strategies")
    if not isinstance(strategies, list):
        return
    for strat in strategies:
        if not isinstance(strat, dict):
            continue
        sid = strat.get("strategy_id")
        per_fold = strat.get("per_fold_metrics")
        if not isinstance(sid, str) or not isinstance(per_fold, list):
            continue
        for entry in per_fold:
            if not isinstance(entry, dict):
                continue
            fi = entry.get("fold_index")
            if not isinstance(fi, int):
                continue
            cached = baseline_cache.get((sid, fi))
            if cached is None:
                cached = _empty_per_strategy_baseline()
            entry["same_secondary_baseline"] = dict(cached)


# ---------------------------------------------------------------------------
# Top-level run helper + CLI
# ---------------------------------------------------------------------------


def build_adapter_inputs(
    secondaries: Sequence[str],
    *,
    stackbuilder_root: str = DEFAULT_STACKBUILDER_ROOT,
    stable_dir: str = DEFAULT_STABLE_DIR,
    price_cache_dir: str = DEFAULT_PRICE_CACHE_DIR,
    cache_dir: str = DEFAULT_CACHE_DIR,
) -> Dict[str, _SecondaryInputs]:
    """Load per-secondary cutoff-safe upstream inputs once before
    walk-forward iteration.
    """
    out: Dict[str, _SecondaryInputs] = {}
    for sec in secondaries:
        sec_norm = str(sec).strip().upper()
        out[sec_norm] = _load_secondary_inputs(
            sec_norm,
            stackbuilder_root=stackbuilder_root,
            stable_dir=stable_dir,
            price_cache_dir=price_cache_dir,
            cache_dir=cache_dir,
        )
    return out


def run_validation(
    adapter: K6MtfValidationAdapter,
    *,
    run_id: Optional[str] = None,
    output_dir: Optional[Path] = None,
    alpha: float = DEFAULT_ALPHA,
    initial_train_days: int = DEFAULT_INITIAL_TRAIN_DAYS,
    test_window_days: int = DEFAULT_TEST_WINDOW_DAYS,
    step_days: int = DEFAULT_STEP_DAYS,
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
    n_bootstrap_samples: int = DEFAULT_N_BOOTSTRAP_SAMPLES,
    borderline_tolerance_multiplier: float = (
        DEFAULT_BORDERLINE_TOLERANCE_MULTIPLIER
    ),
    rng_seed: Optional[int] = None,
    allow_overwrite: bool = False,
) -> Dict[str, Any]:
    """Drive ``validate_strategy_set`` against ``adapter`` and persist
    the sidecar to ``output_dir / validation.json``.

    Returns a dict with ``run_id``, ``sidecar_path``, ``contract``,
    and ``artifact_hash``.
    """
    if run_id is None:
        run_id = generate_run_id(
            K6_MTF_PRODUCER_ENGINE, K6_MTF_APP_SURFACE,
        )
    if output_dir is None:
        base = resolve_validation_output_base()
        output_dir = base / run_id
    output_dir = Path(output_dir)

    history_index = adapter.history_index()
    contract = validate_strategy_set(
        adapter, history_index,
        run_id=run_id,
        producer_engine=K6_MTF_PRODUCER_ENGINE,
        app_surface=K6_MTF_APP_SURFACE,
        alpha=alpha,
        initial_train_days=initial_train_days,
        test_window_days=test_window_days,
        step_days=step_days,
        n_permutations=n_permutations,
        n_bootstrap_samples=n_bootstrap_samples,
        borderline_tolerance_multiplier=borderline_tolerance_multiplier,
        rng_seed=rng_seed,
    )
    # Persist per-(strategy, fold) same-secondary buy-and-hold into the
    # contract dict BEFORE write_validation_sidecar serializes it.
    # validation_engine v1 does not carry adapter-local
    # StrategyFoldResult.metadata into the sidecar, so the locked 5C-1
    # Section 6 same-secondary baseline evidence would otherwise be
    # lost on persistence. baseline_for_fold remains deliberately
    # empty (so engine-level per_fold_baseline_delta entries stay None
    # rather than becoming misleading blended values).
    _enrich_contract_with_same_secondary_baseline(
        contract, adapter._fold_baseline_cache,
    )
    sidecar_path = write_validation_sidecar(
        contract, output_dir, allow_overwrite=allow_overwrite,
    )
    artifact_hash = compute_validation_artifact_hash(sidecar_path)
    return {
        "run_id": run_id,
        "sidecar_path": str(sidecar_path),
        "contract": contract,
        "artifact_hash": artifact_hash,
    }


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="utils.k6_mtf_validation.adapter",
        description=(
            "K=6 MTF SelectionAdapter for validation_contract_v1. "
            "Honestly recomputes K=6 MTF ranking-row evidence per "
            "walk-forward fold from cutoff-safe upstream inputs. Reads "
            "zero bytes from output/k6_mtf/** as validation evidence."
        ),
    )
    parser.add_argument(
        "--secondaries", required=False,
        default=",".join(_DEFAULT_LAUNCH_FAMILY),
        help=(
            "Comma-separated launch-family secondaries. Default = "
            "the K=6 MTF Tier 1 universe."
        ),
    )
    parser.add_argument(
        "--stackbuilder-root", default=DEFAULT_STACKBUILDER_ROOT,
        help="Upstream StackBuilder selected_build root.",
    )
    parser.add_argument(
        "--signal-library-dir", default=DEFAULT_STABLE_DIR,
        help="Per-(member, timeframe) signal library root.",
    )
    parser.add_argument(
        "--price-cache-dir", default=DEFAULT_PRICE_CACHE_DIR,
        help="Per-secondary daily-close price cache root.",
    )
    parser.add_argument(
        "--cache-dir", default=DEFAULT_CACHE_DIR,
        help="Fallback Spymaster results cache root.",
    )
    parser.add_argument(
        "--run-id", default=None,
        help="Explicit run_id (default = auto-generated).",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help=(
            "Explicit sidecar output directory. Default resolves "
            "VALIDATION_OUTPUT_BASE_DIR safely from either "
            "<PROJECT_DIR> or <REPO_ROOT> cwd."
        ),
    )
    parser.add_argument(
        "--alpha", type=float, default=DEFAULT_ALPHA,
    )
    parser.add_argument(
        "--initial-train-days", type=int,
        default=DEFAULT_INITIAL_TRAIN_DAYS,
    )
    parser.add_argument(
        "--test-window-days", type=int,
        default=DEFAULT_TEST_WINDOW_DAYS,
    )
    parser.add_argument(
        "--step-days", type=int, default=DEFAULT_STEP_DAYS,
    )
    parser.add_argument(
        "--n-permutations", type=int, default=DEFAULT_N_PERMUTATIONS,
    )
    parser.add_argument(
        "--n-bootstrap-samples", type=int,
        default=DEFAULT_N_BOOTSTRAP_SAMPLES,
    )
    parser.add_argument(
        "--borderline-tolerance-multiplier", type=float,
        default=DEFAULT_BORDERLINE_TOLERANCE_MULTIPLIER,
    )
    parser.add_argument(
        "--rng-seed", type=int, default=None,
    )
    parser.add_argument(
        "--allow-overwrite", action="store_true", default=False,
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    secondaries = [
        s.strip().upper() for s in args.secondaries.split(",")
        if s.strip()
    ]
    inputs = build_adapter_inputs(
        secondaries,
        stackbuilder_root=args.stackbuilder_root,
        stable_dir=args.signal_library_dir,
        price_cache_dir=args.price_cache_dir,
        cache_dir=args.cache_dir,
    )
    adapter = K6MtfValidationAdapter(
        secondaries=secondaries, secondary_inputs=inputs,
    )
    output_dir = Path(args.output_dir) if args.output_dir else None
    result = run_validation(
        adapter,
        run_id=args.run_id,
        output_dir=output_dir,
        alpha=args.alpha,
        initial_train_days=args.initial_train_days,
        test_window_days=args.test_window_days,
        step_days=args.step_days,
        n_permutations=args.n_permutations,
        n_bootstrap_samples=args.n_bootstrap_samples,
        borderline_tolerance_multiplier=(
            args.borderline_tolerance_multiplier
        ),
        rng_seed=args.rng_seed,
        allow_overwrite=args.allow_overwrite,
    )
    print(json.dumps({
        "run_id": result["run_id"],
        "sidecar_path": result["sidecar_path"],
        "artifact_hash": result["artifact_hash"],
        "validation_status": result["contract"]["validation_status"],
        "n_strategies_tested": result["contract"]["n_strategies_tested"],
        "n_strategies_reported": result["contract"]["n_strategies_reported"],
        "walk_forward_n_folds": result["contract"]["walk_forward_n_folds"],
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
