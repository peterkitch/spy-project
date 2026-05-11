"""Phase 6E-4: extracted Signal Engine SMA-pair optimizer.

This module isolates the daily best-buy / best-short
SMA-pair optimizer that today lives inside Spymaster's Dash
callback (``project/spymaster.py``, around the
``_compute_daily_top_pairs_vectorized`` closure and
``calculate_cumulative_combined_capture``). The Phase 6E-3
audit established that the refresher CLI cannot produce a
production-safe Signal Engine cache while this math remains
trapped inside the Dash app; Phase 6E-4 lifts the math out
into a pure, offline, importable helper.

**This PR does NOT release the data_only_v1 write guard.**
It only extracts and validates the optimizer. A separate
follow-up PR wires the helper into
``signal_engine_cache_refresher.py``.

Strictly offline / side-effect-free:

  - No ``yfinance`` import.
  - No ``spymaster`` / ``dash`` / ``plotly`` import.
  - No web-tier import (``daily_signal_board`` etc.).
  - No disk writes.
  - No production cache mutation.

Public surface
--------------

    SignalEngineSmaOptimizationResult       # dataclass
    optimize_signal_engine_sma_pairs(
        preprocessed_data: pd.DataFrame,
        *,
        ticker: Optional[str] = None,
        max_sma_day: int = 30,
    ) -> SignalEngineSmaOptimizationResult

Stable issue codes surfaced via ``result.issue_codes``:

  - ``invalid_preprocessed_data`` — input is not a usable
    DataFrame or lacks a ``Close`` column / valid index.
  - ``insufficient_history`` — fewer than two rows of price
    data; the shifted-by-one signal contract cannot fire.
  - ``invalid_max_sma_day`` — ``max_sma_day`` < 2.

Algorithm summary
-----------------

Spymaster's optimizer searches every ordered SMA pair
``(i, j)`` with ``1 <= i, j <= max_sma_day`` and ``i != j``.
For each pair:

  * the buy signal on day ``t`` is
    ``SMA_i[t-1] > SMA_j[t-1]`` (yesterday's SMAs decide
    today's position);
  * the short signal on day ``t`` is
    ``SMA_i[t-1] < SMA_j[t-1]``;
  * cumulative buy capture is the running sum of
    ``signal * daily_return * 100`` where
    ``daily_return = closes[t] / closes[t-1] - 1`` with
    NaN / inf / zero-prev-close coerced to 0;
  * cumulative short capture flips the sign.

Each day records the (pair, cumulative-capture) tuple for
the buy side and the short side. Tie-break: among pairs
with equal cumulative capture (within ``EPS = 1e-12``), the
later global pair index wins — matching Spymaster's
right-most-max behavior at ``spymaster.py:5076-5092``.

Any day that ends with a ``(0, 0)`` best-pair sentinel is
back-filled with the MAX-SMA sentinels Spymaster's writer
expects: ``(msd, msd - 1)`` for buy and
``(msd - 1, msd)`` for short. Spymaster's writer rejects
``(0, 0)`` payloads as corrupted; the sentinels keep the
contract satisfied.

The per-day ``active_pairs`` are built from the same
"previous-day signal verification" rule
``calculate_cumulative_combined_capture`` uses
(``spymaster.py:7617``): yesterday's best buy / short pair
is re-checked against yesterday's SMAs and today's position
is one of ``"Buy a,b"`` / ``"Short a,b"`` / ``"None"``.

The returned ``preprocessed_data`` carries the original
``Close`` column plus a fresh ``SMA_1`` … ``SMA_max_sma_day``
matrix. If the input already had those columns the optimizer
reuses them verbatim so a saved Spymaster cache round-trips
without numerical drift.

Spymaster behaviors preserved
-----------------------------

  * SMA construction: ``Close.rolling(window=j,
    min_periods=j, center=False).mean()`` (``spymaster.py:4929``).
  * Returns vector: ``Close.pct_change(fill_method=None)``
    with ``±inf -> NaN -> 0`` (``spymaster.py:4972``).
  * Pair enumeration order and right-most tie-break
    (``spymaster.py:5036-5092``).
  * ``(0, 0)`` -> MAX-SMA sentinel back-fill
    (``spymaster.py:5100-5111``).
  * ``_align_pairs_to_calendar`` semantics
    (``spymaster.py:7576``).
  * ``calculate_cumulative_combined_capture`` per-day rule
    (``spymaster.py:7649-7710``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Issue codes
# ---------------------------------------------------------------------------

ISSUE_INVALID_PREPROCESSED_DATA = "invalid_preprocessed_data"
ISSUE_INSUFFICIENT_HISTORY = "insufficient_history"
ISSUE_INVALID_MAX_SMA_DAY = "invalid_max_sma_day"


# Tie-break / equality tolerance. Matches Spymaster's
# ``EPS = 1e-12`` at ``spymaster.py:5017``.
_EPS = 1e-12


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SignalEngineSmaOptimizationResult:
    """The shape downstream code (today: the Phase 6E-3
    refresher in a future wiring PR) needs to build a
    production-safe Signal Engine cache payload."""

    ticker: Optional[str]
    preprocessed_data: pd.DataFrame
    daily_top_buy_pairs: dict[pd.Timestamp, tuple[tuple[int, int], float]]
    daily_top_short_pairs: dict[pd.Timestamp, tuple[tuple[int, int], float]]
    cumulative_combined_captures: pd.Series
    active_pairs: list[str]
    top_buy_pair: Optional[tuple[int, int]]
    top_short_pair: Optional[tuple[int, int]]
    top_buy_capture: float
    top_short_capture: float
    last_processed_date: Optional[pd.Timestamp]
    existing_max_sma_day: int
    issue_codes: tuple[str, ...] = field(default_factory=tuple)


def _failure(
    ticker: Optional[str], max_sma_day: int,
    issue_codes: tuple[str, ...],
) -> SignalEngineSmaOptimizationResult:
    return SignalEngineSmaOptimizationResult(
        ticker=ticker,
        preprocessed_data=pd.DataFrame(),
        daily_top_buy_pairs={},
        daily_top_short_pairs={},
        cumulative_combined_captures=pd.Series(dtype=float),
        active_pairs=[],
        top_buy_pair=None,
        top_short_pair=None,
        top_buy_capture=0.0,
        top_short_capture=0.0,
        last_processed_date=None,
        existing_max_sma_day=int(max_sma_day),
        issue_codes=issue_codes,
    )


# ---------------------------------------------------------------------------
# Internal helpers (extracted from spymaster.py without
# importing the Dash app)
# ---------------------------------------------------------------------------


def _coerce_close_series(df: pd.DataFrame) -> Optional[pd.Series]:
    """Return a clean ``Close`` series (float64, monotonic
    datetime index, no duplicates). ``None`` on failure.

    Mirrors Spymaster's defensive coercion at
    ``spymaster.py:4900-4919``."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    if "Close" not in df.columns:
        return None
    s = df["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s = pd.to_numeric(s, errors="coerce").astype(np.float64)
    s = s.copy()
    try:
        s.index = pd.to_datetime(s.index, errors="coerce")
    except Exception:
        return None
    s = s.dropna()
    if s.empty:
        return None
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def _build_sma_matrix(
    closes: pd.Series, max_sma_day: int,
    existing_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Materialize the (n_days, max_sma_day) SMA matrix.

    If every ``SMA_<k>`` column for ``k`` in ``1..max_sma_day``
    is already on ``existing_df`` with the same index as
    ``closes``, reuse them verbatim — that is the parity
    contract for saved Spymaster caches. Otherwise compute
    via ``rolling(window=j, min_periods=j, center=False).mean()``
    (Spymaster's ``spymaster.py:4929`` recipe).
    """
    sma_columns = [f"SMA_{j}" for j in range(1, max_sma_day + 1)]
    if (
        existing_df is not None
        and all(c in existing_df.columns for c in sma_columns)
        and existing_df.index.equals(closes.index)
    ):
        return existing_df[sma_columns].astype(np.float64)
    sma_data: dict[str, pd.Series] = {}
    for j in range(1, max_sma_day + 1):
        sma_data[f"SMA_{j}"] = closes.rolling(
            window=j, min_periods=j, center=False,
        ).mean()
    return pd.DataFrame(sma_data, index=closes.index)


def _safe_returns(closes: np.ndarray) -> np.ndarray:
    """Spymaster ``spymaster.py:4972-4976`` returns vector:
    ``pct_change(fill_method=None)`` with ``±inf -> NaN -> 0``
    and an explicit 0 for day 0."""
    n = len(closes)
    out = np.zeros(n, dtype=np.float64)
    if n < 2:
        return out
    prev = closes[:-1]
    curr = closes[1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = curr / prev - 1.0
    safe = (
        np.isfinite(prev) & np.isfinite(curr) & (prev != 0.0)
    )
    out[1:] = np.where(safe, raw, 0.0)
    out = np.where(np.isfinite(out), out, 0.0)
    return out


def _enumerate_pairs(max_sma_day: int) -> list[tuple[int, int]]:
    """Enumerate every ordered ``(i, j)`` with
    ``1 <= i, j <= max_sma_day`` and ``i != j``, in the
    exact order Spymaster materializes them at
    ``spymaster.py:5036-5042``:

        for pc_global in range(N * (N-1)):
            i = (pc_global // (N-1)) + 1
            j = (pc_global % (N-1)) + 1
            j = j if j < i else j + 1
    """
    out: list[tuple[int, int]] = []
    total = max_sma_day * (max_sma_day - 1)
    for pc in range(total):
        i = (pc // (max_sma_day - 1)) + 1
        j = (pc % (max_sma_day - 1)) + 1
        if j >= i:
            j += 1
        out.append((i, j))
    return out


def _asof(series: pd.Series, target: Any, default: Any = None) -> Any:
    """Tolerant ``as-of`` lookup matching the helper at
    ``spymaster.py:3952``: exact match first, otherwise the
    last value with index ``<= target``, else ``default``."""
    try:
        if target in series.index:
            return series.loc[target]
    except Exception:
        pass
    try:
        valid = series.index[series.index <= target]
        if len(valid) == 0:
            return default
        return series.loc[valid[-1]]
    except Exception:
        return default


def _align_pairs_to_calendar(
    df_index: pd.DatetimeIndex,
    daily_top_buy_pairs: dict,
    daily_top_short_pairs: dict,
    max_sma_day: int,
) -> tuple[dict, dict]:
    """Port of Spymaster's ``_align_pairs_to_calendar``
    (``spymaster.py:7576``). Ensure every trading day has a
    valid ``((pair_i, pair_j), capture)`` tuple; treat
    ``(0, 0)`` as missing and ffill/bfill from nearest
    valid day; if everything is invalid, seed with MAX-SMA
    sentinels."""
    cal = pd.DatetimeIndex(df_index).normalize()

    def _to_series(dct: dict) -> pd.Series:
        return pd.Series({
            pd.Timestamp(k).normalize(): v
            for k, v in dct.items()
        })

    def _mask_invalid(series: pd.Series) -> pd.Series:
        def _is_bad(v: Any) -> bool:
            try:
                p, _ = v
                return isinstance(p, tuple) and p == (0, 0)
            except Exception:
                return True
        return series.where(~series.apply(_is_bad), pd.NA)

    buy_s = _to_series(daily_top_buy_pairs).reindex(cal)
    shr_s = _to_series(daily_top_short_pairs).reindex(cal)
    buy_s = _mask_invalid(buy_s).ffill().bfill()
    shr_s = _mask_invalid(shr_s).ffill().bfill()

    msd = int(max_sma_day)
    if buy_s.isna().all():
        buy_s = pd.Series(
            [((msd, msd - 1), 0.0)] * len(cal), index=cal,
        )
    if shr_s.isna().all():
        shr_s = pd.Series(
            [((msd - 1, msd), 0.0)] * len(cal), index=cal,
        )

    buy_aligned = {d: buy_s.loc[d] for d in cal}
    shr_aligned = {d: shr_s.loc[d] for d in cal}
    return buy_aligned, shr_aligned


def _compute_cumulative_combined_capture(
    df: pd.DataFrame,
    daily_top_buy_pairs: dict,
    daily_top_short_pairs: dict,
    max_sma_day: int,
) -> tuple[pd.Series, list[str]]:
    """Port of ``calculate_cumulative_combined_capture``
    (``spymaster.py:7617``) without the progress bar / log
    side effects. Returns
    ``(cumulative_combined_captures, active_pairs)``."""
    if not daily_top_buy_pairs or not daily_top_short_pairs:
        return (
            pd.Series([0.0], index=[df.index[0]]),
            ["None"],
        )
    msd = int(max_sma_day)
    daily_top_buy_pairs, daily_top_short_pairs = (
        _align_pairs_to_calendar(
            df.index,
            daily_top_buy_pairs,
            daily_top_short_pairs,
            msd,
        )
    )
    dates = list(pd.DatetimeIndex(df.index).normalize())
    if not dates:
        return (
            pd.Series([0.0], index=[df.index[0]]),
            ["None"],
        )

    close_series = pd.Series(
        df["Close"].astype(np.float64).to_numpy(),
        index=pd.DatetimeIndex(df.index).normalize(),
    )

    cumulative_captures: list[float] = []
    active_pairs: list[str] = []
    cumulative_capture = 0.0

    for i, current_date in enumerate(dates):
        if i == 0:
            current_position = "None"
            daily_capture = 0.0
        else:
            previous_date = dates[i - 1]
            prev_buy_pair, prev_buy_capture = (
                daily_top_buy_pairs[previous_date]
            )
            prev_short_pair, prev_short_capture = (
                daily_top_short_pairs[previous_date]
            )
            if prev_buy_pair == (0, 0):
                prev_buy_pair = (msd, msd - 1)
            if prev_short_pair == (0, 0):
                prev_short_pair = (msd - 1, msd)

            sma_buy_0 = _asof(
                df[f"SMA_{prev_buy_pair[0]}"],
                previous_date, default=0.0,
            )
            sma_buy_1 = _asof(
                df[f"SMA_{prev_buy_pair[1]}"],
                previous_date, default=0.0,
            )
            buy_signal = bool(sma_buy_0 > sma_buy_1)

            sma_short_0 = _asof(
                df[f"SMA_{prev_short_pair[0]}"],
                previous_date, default=0.0,
            )
            sma_short_1 = _asof(
                df[f"SMA_{prev_short_pair[1]}"],
                previous_date, default=0.0,
            )
            short_signal = bool(sma_short_0 < sma_short_1)

            if buy_signal and short_signal:
                if prev_buy_capture > prev_short_capture:
                    current_position = (
                        f"Buy {prev_buy_pair[0]},{prev_buy_pair[1]}"
                    )
                else:
                    current_position = (
                        f"Short {prev_short_pair[0]},"
                        f"{prev_short_pair[1]}"
                    )
            elif buy_signal:
                current_position = (
                    f"Buy {prev_buy_pair[0]},{prev_buy_pair[1]}"
                )
            elif short_signal:
                current_position = (
                    f"Short {prev_short_pair[0]},{prev_short_pair[1]}"
                )
            else:
                current_position = "None"

            prev_close = _asof(
                close_series, previous_date, default=np.nan,
            )
            curr_close = _asof(
                close_series, current_date, default=np.nan,
            )
            try:
                prev_f = float(prev_close)
                curr_f = float(curr_close)
            except Exception:
                prev_f = float("nan")
                curr_f = float("nan")
            if (
                np.isfinite(prev_f)
                and np.isfinite(curr_f)
                and prev_f != 0.0
            ):
                daily_return = curr_f / prev_f - 1.0
            else:
                daily_return = 0.0

            if current_position.startswith("Buy"):
                daily_capture = daily_return * 100.0
            elif current_position.startswith("Short"):
                daily_capture = -daily_return * 100.0
            else:
                daily_capture = 0.0

        cumulative_capture += daily_capture
        cumulative_captures.append(cumulative_capture)
        active_pairs.append(current_position)

    return (
        pd.Series(cumulative_captures, index=dates),
        active_pairs,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def optimize_signal_engine_sma_pairs(
    preprocessed_data: pd.DataFrame,
    *,
    ticker: Optional[str] = None,
    max_sma_day: int = 30,
) -> SignalEngineSmaOptimizationResult:
    """Run the Spymaster daily best-buy / best-short SMA-pair
    optimization over ``preprocessed_data``.

    Returns a ``SignalEngineSmaOptimizationResult`` carrying
    every field a future caller (the refresher's wiring PR)
    needs to build a production-safe Signal Engine cache
    payload. See module docstring for the algorithm summary
    and the exact Spymaster references each behavior is
    pinned to.
    """
    msd = int(max_sma_day)
    if msd < 2:
        return _failure(
            ticker, msd, (ISSUE_INVALID_MAX_SMA_DAY,),
        )

    closes = _coerce_close_series(preprocessed_data)
    if closes is None:
        return _failure(
            ticker, msd, (ISSUE_INVALID_PREPROCESSED_DATA,),
        )
    if len(closes) < 2:
        return _failure(
            ticker, msd, (ISSUE_INSUFFICIENT_HISTORY,),
        )

    sma_df = _build_sma_matrix(
        closes, msd,
        existing_df=preprocessed_data
        if isinstance(preprocessed_data, pd.DataFrame)
        else None,
    )
    sma_df = sma_df.reindex(closes.index)
    sma_matrix = sma_df.to_numpy(dtype=np.float64, copy=False)

    # Build the augmented DataFrame in one concat to avoid
    # pandas' DataFrame-fragmentation warning on the
    # max_sma_day=114 SPY case (115 columns total).
    close_frame = pd.DataFrame(
        {"Close": closes.astype(np.float64)},
        index=closes.index,
    )
    df_out = pd.concat(
        [close_frame, sma_df.astype(np.float64)], axis=1,
    )

    closes_arr = closes.to_numpy(dtype=np.float64, copy=False)
    returns = _safe_returns(closes_arr)
    n_days = len(closes)

    pairs = _enumerate_pairs(msd)

    buy_best_val = np.full(n_days, -np.inf, dtype=np.float64)
    buy_best_gidx = np.full(n_days, -1, dtype=np.int64)
    buy_best_pair = np.zeros((n_days, 2), dtype=np.int64)
    short_best_val = np.full(n_days, -np.inf, dtype=np.float64)
    short_best_gidx = np.full(n_days, -1, dtype=np.int64)
    short_best_pair = np.zeros((n_days, 2), dtype=np.int64)

    for g_idx, (i, j) in enumerate(pairs):
        sma_i = sma_matrix[:, i - 1]
        sma_j = sma_matrix[:, j - 1]
        # Shift by 1: day 0 has no position, day t looks at
        # SMAs at t-1.
        cmp = sma_i[:-1] > sma_j[:-1]
        buy_sig = np.concatenate(([False], cmp))
        cmp_s = sma_i[:-1] < sma_j[:-1]
        short_sig = np.concatenate(([False], cmp_s))

        # NaN-safe: when either SMA at t-1 is NaN, both
        # comparisons return False. (NumPy comparisons with
        # NaN already return False; this is explicit
        # documentation only.)
        buy_cap = np.cumsum(
            buy_sig.astype(np.float64) * returns * 100.0,
        )
        short_cap = np.cumsum(
            short_sig.astype(np.float64) * (-returns) * 100.0,
        )

        better_buy = buy_cap > buy_best_val + _EPS
        equal_buy = (
            (np.abs(buy_cap - buy_best_val) <= _EPS)
            & (g_idx > buy_best_gidx)
        )
        upd_buy = better_buy | equal_buy
        if upd_buy.any():
            buy_best_val[upd_buy] = buy_cap[upd_buy]
            buy_best_gidx[upd_buy] = g_idx
            buy_best_pair[upd_buy, 0] = i
            buy_best_pair[upd_buy, 1] = j

        better_short = short_cap > short_best_val + _EPS
        equal_short = (
            (np.abs(short_cap - short_best_val) <= _EPS)
            & (g_idx > short_best_gidx)
        )
        upd_short = better_short | equal_short
        if upd_short.any():
            short_best_val[upd_short] = short_cap[upd_short]
            short_best_gidx[upd_short] = g_idx
            short_best_pair[upd_short, 0] = i
            short_best_pair[upd_short, 1] = j

    # Spymaster's writer rejects (0, 0) pairs as corrupted
    # cache payloads. Back-fill any day that was never
    # updated (e.g. early rows where every pair's
    # cumulative capture was still -inf vs a finite zero
    # comparison) with MAX-SMA sentinels.
    zero_buy = (
        (buy_best_pair[:, 0] == 0)
        & (buy_best_pair[:, 1] == 0)
    )
    zero_short = (
        (short_best_pair[:, 0] == 0)
        & (short_best_pair[:, 1] == 0)
    )
    if zero_buy.any():
        buy_best_pair[zero_buy] = (msd, msd - 1)
        buy_best_val[zero_buy] = 0.0
    if zero_short.any():
        short_best_pair[zero_short] = (msd - 1, msd)
        short_best_val[zero_short] = 0.0

    daily_top_buy_pairs: dict = {}
    daily_top_short_pairs: dict = {}
    for d, date in enumerate(closes.index):
        daily_top_buy_pairs[date] = (
            (int(buy_best_pair[d, 0]), int(buy_best_pair[d, 1])),
            float(buy_best_val[d]),
        )
        daily_top_short_pairs[date] = (
            (int(short_best_pair[d, 0]), int(short_best_pair[d, 1])),
            float(short_best_val[d]),
        )

    cumulative_combined_captures, active_pairs = (
        _compute_cumulative_combined_capture(
            df_out, daily_top_buy_pairs,
            daily_top_short_pairs, msd,
        )
    )

    last_day = closes.index[-1]
    top_buy_pair = daily_top_buy_pairs[last_day][0]
    top_short_pair = daily_top_short_pairs[last_day][0]
    top_buy_capture = float(daily_top_buy_pairs[last_day][1])
    top_short_capture = float(daily_top_short_pairs[last_day][1])

    return SignalEngineSmaOptimizationResult(
        ticker=ticker,
        preprocessed_data=df_out,
        daily_top_buy_pairs=daily_top_buy_pairs,
        daily_top_short_pairs=daily_top_short_pairs,
        cumulative_combined_captures=cumulative_combined_captures,
        active_pairs=active_pairs,
        top_buy_pair=top_buy_pair,
        top_short_pair=top_short_pair,
        top_buy_capture=top_buy_capture,
        top_short_capture=top_short_capture,
        last_processed_date=last_day,
        existing_max_sma_day=msd,
        issue_codes=(),
    )
