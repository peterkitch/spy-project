"""Phase 6I-21: read-only multi-window K engine core evaluator.

The first real engine-core slice of the future TrafficFlow-style
multi-window K engine. This module is NOT a bridge / projection
and NOT a Confluence presentation adapter. It computes true
per-window K metrics from window-specific in-memory inputs so
later phases can write the Phase 6I-20-defined
``per_window_k_metrics`` covering the canonical 60-cell grid
(``K = 1..12`` × ``window ∈ {1d, 1wk, 1mo, 3mo, 1y}``).

What this module IS
-------------------

A pure-Python read-only evaluator for one ``(K, window)`` cell,
plus a grid helper that evaluates many cells from one bundle of
per-window inputs. Inputs are supplied in-memory by the caller:

  * ``target_ticker`` — the build's target symbol;
  * ``K`` — minimum agreeing active member count required to
    fire (1..12 in the canonical grid; the math accepts any
    positive int);
  * ``window`` — the window the inputs already correspond to
    (``"1d"``, ``"1wk"``, ``"1mo"``, ``"3mo"``, ``"1y"``, or
    any other label the caller wants to attach);
  * ``dates`` — sequence of dates aligned 1-to-1 with
    ``target_close``;
  * ``target_close`` — close prices for the target on each of
    those dates, already at the window's bar frequency;
  * ``member_signal_columns`` — mapping of member ticker to a
    sequence of pre-protocol ``Buy / Short / None / "missing"``
    strings, one per bar in ``dates``;
  * ``member_protocols`` (optional) — mapping of member ticker
    to ``"D"`` (Direct, default) or ``"I"`` (Inverse); applied
    per the canonical PRJCT9 protocol rule.

What this module IS NOT
-----------------------

  * NOT a projection / bridge. It does NOT resample daily
    signals onto longer windows. Every window's inputs must
    arrive *already at the window's bar frequency*. The
    Phase 6D-2 ``trafficflow_multitimeframe_bridge`` is a
    different module that projects daily signals via
    ``pandas.resample().last() + ffill``; this module never
    invokes that path.
  * NOT a Confluence presentation adapter. It does not consume
    Phase 6I-3 ranking rows or the Phase 6I-19 decision-brief
    surface.
  * NOT wired into the production pipeline. Phase 6I-21 ships
    the core math + data shape only. No writer / refresher /
    pipeline runner is touched.
  * Does NOT yet make production
    ``has_true_multiwindow_k_engine_outputs=True``. The
    Phase 6I-20 gap audit will continue to report the existing
    Confluence artifact as a daily-only / projection layer
    until a later phase actually emits the
    ``per_window_k_metrics`` field on the on-disk artifact.
  * Does NOT close the carry-forward evidence gaps
    (``real_confluence_pipeline_runner_write``,
    ``real_post_pipeline_validation_on_writer_path``,
    writer-surface provider telemetry).

Semantics (reused from the existing local engine family)
--------------------------------------------------------

  * **Direct / Inverse protocol** — applied per member before
    the combine: Direct passes the raw ``Buy / Short / None``
    through; Inverse swaps ``Buy <-> Short`` and maps anything
    else to ``None``. The protocol logic is re-derived locally
    in this module rather than importing
    ``research_artifacts._apply_protocol`` (private helper);
    the rule is identical.
  * **Combine** — delegated verbatim to the public
    ``research_artifacts.combine_member_signals`` function:
    K-thresholded strict unanimity over active members; any
    mix of Buy and Short collapses to None; below-threshold
    agreement collapses to None.
  * **Capture math** — per-bar daily capture: Buy bar maps to
    ``+pct_change(target_close) * 100``; Short bar maps to
    ``-pct_change(target_close) * 100``; None bar maps to
    ``0.0``. Trigger days are Buy + Short bars.
  * **Aggregates** — ``trigger_days = #trigger bars``,
    ``total_capture_pct = sum(trigger capture)``,
    ``avg_daily_capture_pct = mean(trigger capture)``,
    ``sharpe_ratio = avg / std`` over trigger bars (sample
    std, ddof=1; returns 0.0 when ``trigger_days <= 1`` or
    std is zero), ``wins / losses = #positive / #negative
    trigger captures``.
  * **No persist-skip in the core.** Persist-skip / T-1 trim
    is an upstream artifact-layer concern (the Phase 6D-1
    daily-K builder already trims 1 bar by default). The
    core operates on the bars it is given; the caller is
    responsible for any trimming before invocation.

Production-relevant input shape (Phase 6I-21 Codex amendment)
-------------------------------------------------------------

Real StackBuilder builds emit one row per K value, and each K
row may have a different ``members_str`` -- the K=1 row's
member set is not necessarily the K=2 row's member set. The
production-relevant evaluator is therefore
``evaluate_k_window_grid``, which accepts a per-``(K, window)``
input map so each cell can carry its own member set, dates,
target closes, and protocols.

``evaluate_grid`` (the older same-member-set helper) is kept
as a convenience for tests / coverage smokes where every K
row uses the same member bundle per window; do NOT use it on
real StackBuilder data.

Public surface
--------------

    CANONICAL_WINDOWS                  # tuple[str, ...]
    CANONICAL_K_VALUES                 # tuple[int, ...]

    PROTOCOL_DIRECT                    # "D"
    PROTOCOL_INVERSE                   # "I"

    SIGNAL_BUY / SIGNAL_SHORT          # "Buy" / "Short"
    SIGNAL_NONE / SIGNAL_MISSING       # "None" / "missing"

    @dataclass(frozen=True) PerWindowKCell

    evaluate_cell(                     # primitive
        *, target_ticker, K, window,
        dates, target_close,
        member_signal_columns,
        member_protocols=None,
    ) -> PerWindowKCell

    evaluate_k_window_grid(            # production-relevant grid
        *, target_ticker,
        per_cell_inputs,               # Mapping[(K, window), Mapping]
        member_protocols_default=None,
    ) -> tuple[PerWindowKCell, ...]

    evaluate_grid(                     # same-member-set convenience
        *, target_ticker, K_values, windows,
        per_window_inputs,
        member_protocols=None,
    ) -> tuple[PerWindowKCell, ...]

    cells_to_per_window_k_metrics_payload(
        cells,
    ) -> list[dict[str, Any]]

Strictly read-only
------------------

The load-bearing claims of this module:

  * No ``yfinance`` / ``dash`` import.
  * No live engine import (``trafficflow`` / ``spymaster`` /
    ``impactsearch`` / ``onepass`` / ``confluence`` /
    ``cross_ticker_confluence`` / ``daily_signal_board``).
  * No writer / refresher / pipeline runner.
  * No ``subprocess``.
  * No ``trafficflow_multitimeframe_bridge`` import (this
    module is NOT a projection / bridge).
  * **No projection logic**: no call to ``.resample()`` or
    ``.ffill()`` anywhere in code (AST-verified by tests).
  * No production write at any layer.

Note on the ``pandas`` / ``numpy`` dependency: this module
does NOT itself import ``pandas`` or ``numpy`` at top level,
but it does import ``research_artifacts`` (for the public
``combine_member_signals`` helper), which may transitively
pull those in. The static "no pandas/numpy at top level" check
in the test suite is therefore a check on this module's own
imports only -- the transitive dependency graph is out of
scope. The load-bearing claim is the no-projection / no-live-
engine / no-production-write set above; the lack of a direct
``pandas`` / ``numpy`` import is just an honest restatement
that the core does its math in plain Python loops, not
pandas vectorization.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

import research_artifacts as _ra


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

CANONICAL_WINDOWS: tuple[str, ...] = (
    "1d", "1wk", "1mo", "3mo", "1y",
)
CANONICAL_K_VALUES: tuple[int, ...] = tuple(range(1, 13))

PROTOCOL_DIRECT = "D"
PROTOCOL_INVERSE = "I"

SIGNAL_BUY = "Buy"
SIGNAL_SHORT = "Short"
SIGNAL_NONE = "None"
SIGNAL_MISSING = "missing"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerWindowKCell:
    """One ``(K, window)`` cell of the future per-window K
    engine output.

    The five canonical fields (``K`` / ``window`` /
    ``total_capture_pct`` / ``sharpe_ratio`` / ``trigger_days``)
    are exactly the Phase 6I-20-required keys on the future
    ``per_window_k_metrics`` artifact entries. The remaining
    fields are operator-facing extras (latest signal counts,
    wins / losses, member count, average daily capture); the
    Phase 6I-20 contract tolerates extras on top of the
    required-five set.
    """

    K: int
    window: str
    total_capture_pct: float
    avg_daily_capture_pct: float
    sharpe_ratio: float
    trigger_days: int
    wins: int
    losses: int
    latest_combined_signal: str
    latest_buy_count: int
    latest_short_count: int
    latest_none_count: int
    latest_missing_count: int
    member_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "K": int(self.K),
            "window": str(self.window),
            "total_capture_pct": float(self.total_capture_pct),
            "avg_daily_capture_pct": float(
                self.avg_daily_capture_pct,
            ),
            "sharpe_ratio": float(self.sharpe_ratio),
            "trigger_days": int(self.trigger_days),
            "wins": int(self.wins),
            "losses": int(self.losses),
            "latest_combined_signal": str(
                self.latest_combined_signal,
            ),
            "latest_buy_count": int(self.latest_buy_count),
            "latest_short_count": int(
                self.latest_short_count,
            ),
            "latest_none_count": int(self.latest_none_count),
            "latest_missing_count": int(
                self.latest_missing_count,
            ),
            "member_count": int(self.member_count),
        }


# ---------------------------------------------------------------------------
# Protocol helper
# ---------------------------------------------------------------------------


def _apply_protocol(
    raw_signal: Any, protocol: Optional[str],
) -> str:
    """Apply Direct / Inverse protocol to one raw member
    signal.

    Direct (or unknown protocol code): pass ``Buy / Short /
    None`` through verbatim; collapse anything else to
    ``None``. Inverse: swap ``Buy <-> Short``; map anything
    else to ``None``. Empty / ``"missing"`` raw values map to
    ``SIGNAL_MISSING`` and are returned to the caller to be
    filtered out before combine.

    The rule is identical to ``research_artifacts._apply_
    protocol`` (private helper); re-derived here so the core
    does not depend on a private symbol.
    """
    s = "" if raw_signal is None else str(raw_signal).strip()
    if not s:
        return SIGNAL_MISSING
    low = s.lower()
    if low == "missing":
        return SIGNAL_MISSING
    if (protocol or "").upper() == PROTOCOL_INVERSE:
        if low == "buy":
            return SIGNAL_SHORT
        if low == "short":
            return SIGNAL_BUY
        return SIGNAL_NONE
    if low == "buy":
        return SIGNAL_BUY
    if low == "short":
        return SIGNAL_SHORT
    return SIGNAL_NONE


# ---------------------------------------------------------------------------
# Capture math + sharpe
# ---------------------------------------------------------------------------


def _pct_change_series(
    closes: Sequence[Any],
) -> list[float]:
    """Return per-bar percent-change series in percent units
    (e.g. ``1.0`` for a 1% bar). The first bar is ``0.0``
    by convention. Non-numeric / zero-previous inputs collapse
    to ``0.0`` so a bad row never crashes the evaluator."""
    n = len(closes)
    out: list[float] = []
    for i in range(n):
        if i == 0:
            out.append(0.0)
            continue
        prev = closes[i - 1]
        curr = closes[i]
        try:
            prev_f = float(prev)
            curr_f = float(curr)
        except (TypeError, ValueError):
            out.append(0.0)
            continue
        if prev_f == 0.0:
            out.append(0.0)
            continue
        out.append((curr_f - prev_f) / prev_f * 100.0)
    return out


def _sample_stdev(values: Sequence[float]) -> float:
    """Sample standard deviation (ddof=1). Returns 0.0 for
    fewer than 2 values."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(max(var, 0.0))


# ---------------------------------------------------------------------------
# Per-cell evaluator
# ---------------------------------------------------------------------------


def evaluate_cell(
    *,
    target_ticker: str,
    K: int,
    window: str,
    dates: Sequence[Any],
    target_close: Sequence[Any],
    member_signal_columns: Mapping[str, Sequence[Any]],
    member_protocols: Optional[
        Mapping[str, Optional[str]]
    ] = None,
) -> PerWindowKCell:
    """Evaluate one ``(K, window)`` cell from in-memory
    window-specific inputs.

    ``dates`` is required only for length-alignment; the core
    does not parse or sort dates. Callers must supply inputs
    already at the window's bar frequency in chronological
    order; the per-bar capture math relies on adjacent bars
    being adjacent at the chosen window.

    Returns ``PerWindowKCell`` with the canonical five
    Phase 6I-20-required fields plus operator-facing extras
    (latest signal counts, wins / losses, average daily
    capture, member count).
    """
    n = len(dates)
    if len(target_close) != n:
        raise ValueError(
            f"target_close length {len(target_close)} != "
            f"dates length {n}"
        )
    members: list[str] = list(member_signal_columns.keys())
    for m in members:
        col = member_signal_columns[m]
        if len(col) != n:
            raise ValueError(
                f"member_signal_columns[{m!r}] length "
                f"{len(col)} != dates length {n}"
            )

    proto = dict(member_protocols or {})

    # Per-bar percent-change of target close.
    target_return_pct = _pct_change_series(target_close)

    # Per-bar per-member protocol-applied signals, then
    # combined via the canonical PRJCT9 rule.
    combined_signals: list[str] = []
    buy_counts: list[int] = []
    short_counts: list[int] = []
    none_counts: list[int] = []
    missing_counts: list[int] = []
    for i in range(n):
        per_member: dict[str, str] = {}
        for m in members:
            raw = member_signal_columns[m][i]
            per_member[m] = _apply_protocol(
                raw, proto.get(m),
            )
        active = {
            m: s for m, s in per_member.items()
            if s in (SIGNAL_BUY, SIGNAL_SHORT, SIGNAL_NONE)
        }
        combined = _ra.combine_member_signals(active, K=K)
        combined_signals.append(combined)
        # Counts across ALL members (including missing) for
        # operator-facing diagnostics.
        b = 0
        s_ = 0
        n_ = 0
        miss = 0
        for v in per_member.values():
            if v == SIGNAL_BUY:
                b += 1
            elif v == SIGNAL_SHORT:
                s_ += 1
            elif v == SIGNAL_NONE:
                n_ += 1
            else:
                miss += 1
        buy_counts.append(b)
        short_counts.append(s_)
        none_counts.append(n_)
        missing_counts.append(miss)

    # Per-bar capture.
    trigger_caps: list[float] = []
    for i in range(n):
        sig = combined_signals[i].lower()
        ret = target_return_pct[i]
        if sig == "buy":
            trigger_caps.append(ret)
        elif sig == "short":
            trigger_caps.append(-ret)
        # None bar contributes nothing to total capture and
        # is not a trigger day.

    n_trigger = len(trigger_caps)
    if n_trigger > 0:
        total = float(sum(trigger_caps))
        avg = total / n_trigger
        wins = sum(1 for c in trigger_caps if c > 0)
        losses = sum(1 for c in trigger_caps if c < 0)
    else:
        total = 0.0
        avg = 0.0
        wins = 0
        losses = 0
    if n_trigger > 1:
        std = _sample_stdev(trigger_caps)
        sharpe = (avg / std) if std > 0 else 0.0
    else:
        sharpe = 0.0

    if n > 0:
        latest_combined = combined_signals[-1]
        latest_buy = buy_counts[-1]
        latest_short = short_counts[-1]
        latest_none = none_counts[-1]
        latest_missing = missing_counts[-1]
    else:
        latest_combined = SIGNAL_NONE
        latest_buy = 0
        latest_short = 0
        latest_none = 0
        latest_missing = 0

    return PerWindowKCell(
        K=int(K),
        window=str(window),
        total_capture_pct=float(total),
        avg_daily_capture_pct=float(avg),
        sharpe_ratio=float(sharpe),
        trigger_days=int(n_trigger),
        wins=int(wins),
        losses=int(losses),
        latest_combined_signal=str(latest_combined),
        latest_buy_count=int(latest_buy),
        latest_short_count=int(latest_short),
        latest_none_count=int(latest_none),
        latest_missing_count=int(latest_missing),
        member_count=int(len(members)),
    )


# ---------------------------------------------------------------------------
# Grid evaluator
# ---------------------------------------------------------------------------


def evaluate_grid(
    *,
    target_ticker: str,
    K_values: Iterable[int],
    windows: Iterable[str],
    per_window_inputs: Mapping[
        str, Mapping[str, Any],
    ],
    member_protocols: Optional[
        Mapping[str, Optional[str]]
    ] = None,
) -> tuple[PerWindowKCell, ...]:
    """Same-member-set convenience helper. **NOT the
    StackBuilder production path.**

    Every K value supplied for a given window is evaluated
    against the SAME ``member_signal_columns`` bundle for
    that window. Real StackBuilder builds emit one row per
    K value with potentially different ``members_str`` per
    K row, so the production-relevant shape is per-(K,
    window) -- see ``evaluate_k_window_grid`` below.
    ``evaluate_grid`` is kept as a convenience for tests /
    smoke fixtures where every K row uses the same member
    set; **do not use it on real StackBuilder data**.

    ``per_window_inputs`` maps window -> dict with three
    required keys:

      - ``dates``;
      - ``target_close``;
      - ``member_signal_columns``.

    Windows the caller lists in ``windows`` but does NOT
    supply in ``per_window_inputs`` are silently skipped --
    the core never fabricates a cell. K values for a supplied
    window are evaluated in the order given.

    The returned tuple iterates windows first, then K values,
    in the order supplied. When ``K_values = CANONICAL_K_VALUES``
    AND ``windows = CANONICAL_WINDOWS`` AND every canonical
    window has inputs, exactly 60 cells are emitted, sorted
    ``(1d, K=1..12), (1wk, K=1..12), ..., (1y, K=1..12)``.
    Because every K row shares one member bundle per window,
    cells from this helper are NOT representative of real
    StackBuilder per-K member differences -- use it for
    coverage smokes only.
    """
    cells: list[PerWindowKCell] = []
    K_list = list(K_values)
    for window in windows:
        if window not in per_window_inputs:
            continue
        inputs = per_window_inputs[window]
        dates = inputs.get("dates")
        target_close = inputs.get("target_close")
        member_signal_columns = inputs.get(
            "member_signal_columns",
        )
        if (
            dates is None
            or target_close is None
            or member_signal_columns is None
        ):
            continue
        for K in K_list:
            cells.append(
                evaluate_cell(
                    target_ticker=target_ticker,
                    K=K,
                    window=window,
                    dates=dates,
                    target_close=target_close,
                    member_signal_columns=(
                        member_signal_columns
                    ),
                    member_protocols=member_protocols,
                ),
            )
    return tuple(cells)


# ---------------------------------------------------------------------------
# Per-(K, window) grid evaluator — production-relevant shape
# ---------------------------------------------------------------------------


def evaluate_k_window_grid(
    *,
    target_ticker: str,
    per_cell_inputs: Mapping[
        tuple[int, str], Mapping[str, Any],
    ],
    member_protocols_default: Optional[
        Mapping[str, Optional[str]]
    ] = None,
) -> tuple[PerWindowKCell, ...]:
    """Evaluate per-``(K, window)`` cells where **each cell
    supplies its own inputs**. This is the production-
    relevant shape for the future TrafficFlow-style multi-
    window K engine: real StackBuilder builds emit one row
    per K value, and each K row may have a different
    ``members_str``; each window for that K row has its own
    bar series of member signals.

    ``per_cell_inputs`` maps a ``(K, window)`` tuple to a
    mapping with the following keys:

      - ``dates`` (required) — sequence aligned 1-to-1 with
        ``target_close`` and every member column;
      - ``target_close`` (required) — close prices already
        at the cell's window's bar frequency;
      - ``member_signal_columns`` (required) — mapping of
        member ticker -> sequence of pre-protocol Buy /
        Short / None / "missing" strings, one per bar;
      - ``member_protocols`` (optional) — mapping of member
        ticker -> ``"D"`` / ``"I"``; if absent, falls back
        to the ``member_protocols_default`` argument.

    Cells missing from ``per_cell_inputs`` are silently
    skipped (no fabricated output). Cells whose input dict
    omits any of the three required keys are silently
    skipped (no fabricated output).

    The returned tuple iterates ``per_cell_inputs`` in
    insertion order. To get all 60 canonical cells, the
    caller must supply every ``(K, window)`` pair where
    ``K`` is one of ``CANONICAL_K_VALUES`` AND ``window``
    is one of ``CANONICAL_WINDOWS``.

    This is the function the future production-engine
    glue path should call. The same-member-set
    ``evaluate_grid`` above is a convenience for tests /
    coverage smokes and is NOT representative of real
    StackBuilder per-K member differences.
    """
    cells: list[PerWindowKCell] = []
    for (K, window), inputs in per_cell_inputs.items():
        dates = inputs.get("dates")
        target_close = inputs.get("target_close")
        member_signal_columns = inputs.get(
            "member_signal_columns",
        )
        if (
            dates is None
            or target_close is None
            or member_signal_columns is None
        ):
            continue
        # Per-cell protocols override the default; absent
        # protocols entry falls back to the default; absent
        # default leaves the per-member protocol = None
        # (Direct).
        proto = inputs.get("member_protocols")
        if proto is None:
            proto = member_protocols_default
        cells.append(
            evaluate_cell(
                target_ticker=target_ticker,
                K=K,
                window=window,
                dates=dates,
                target_close=target_close,
                member_signal_columns=(
                    member_signal_columns
                ),
                member_protocols=proto,
            ),
        )
    return tuple(cells)


# ---------------------------------------------------------------------------
# Phase 6I-20 payload helper
# ---------------------------------------------------------------------------


def cells_to_per_window_k_metrics_payload(
    cells: Sequence[PerWindowKCell],
) -> list[dict[str, Any]]:
    """Convert a sequence of ``PerWindowKCell`` into the
    Phase 6I-20-defined ``per_window_k_metrics`` payload shape
    (a list of dicts each carrying the canonical five required
    fields plus this module's extras).

    The Phase 6I-20 gap audit's
    ``_per_window_k_metrics_are_valid`` will accept the
    output as a valid per-window-K-metrics payload when the
    canonical 60-cell coverage is present.
    """
    return [c.to_dict() for c in cells]
