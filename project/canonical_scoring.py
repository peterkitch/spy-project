"""
PRJCT9 canonical scoring (Phase 1B-1).

This module is the single source of truth for the metrics defined in
the v0.5 algorithm spec. It imports no engines and reads no
environment / files. It is intentionally a pure module so engines
can call into it during the Phase 1B-2 rewire without circular
imports or hidden side effects.

Contract reference:
  project/md_library/shared/2026-04-30_PRJCT9_ALGORITHM_SPEC_v0_5.md

Inputs and units:
  - signals: pd.Series of Buy / Short / None labels.
  - returns: pd.Series of decimal close-to-close returns (e.g. +0.01
    for +1%).
  - daily_capture is computed from signals + returns and is in
    PERCENT POINTS (Buy capture = return * 100, Short capture =
    -return * 100, None = 0).
  - avg_daily_capture, total_capture, std_dev, annualized_return,
    annualized_std, and risk_free_rate are all in percent points.

Spec rules implemented here:
  - Trigger days are signal state Buy/Short, not capture != 0.
    A trigger day with daily_capture == 0 counts as a loss
    (deliberate conservative convention; spec §15).
  - std_dev uses ddof=1 by default (spec §16).
  - Sharpe = (avg_daily_capture * periods_per_year - risk_free_rate)
            / (std_dev * sqrt(periods_per_year))
    risk_free_rate default 5.0 (annual percent).
  - p-value uses scipy.stats.t.sf (numerically stable equivalent of
    2 * (1 - cdf(abs(t)))) per spec §17.
  - If trigger_days <= 1 or std_dev == 0, sharpe = 0,
    t_statistic = None, p_value = None.
  - Multi-primary consensus: agreement -> that signal; disagreement
    or all-None -> None (spec §18).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional

import numpy as np
import pandas as pd
from scipy import stats


_BUY = "Buy"
_SHORT = "Short"
_NONE = "None"
_VALID_LABELS = frozenset((_BUY, _SHORT, _NONE))


@dataclass(frozen=True)
class CanonicalScore:
    """
    The full canonical score for a (signals, returns) pair.

    Note: trigger_days, wins, losses count by signal state Buy/Short.
    Zero-capture trigger days count as losses per spec §15.
    """

    trigger_days: int
    wins: int
    losses: int
    win_rate: float
    avg_daily_capture: float
    total_capture: float
    std_dev: float
    sharpe: float
    t_statistic: Optional[float]
    p_value: Optional[float]
    daily_capture: pd.Series
    cumulative_capture: pd.Series


def normalize_signal_series(signals: pd.Series) -> pd.Series:
    """
    Coerce a signal series to canonical labels.

    Accepts string labels (case-sensitive 'Buy'/'Short'/'None'),
    integer codes (+1/-1/0) per the impactsearch/onepass convention,
    or None / NaN (treated as 'None').

    Anything outside the known set is mapped to 'None'.
    """
    out = pd.Series(["None"] * len(signals), index=signals.index, dtype=object)
    for i, raw in enumerate(signals.values):
        if raw is None:
            continue
        if isinstance(raw, (int, np.integer)):
            v = int(raw)
            if v == 1:
                out.iat[i] = _BUY
            elif v == -1:
                out.iat[i] = _SHORT
            else:
                out.iat[i] = _NONE
            continue
        if isinstance(raw, float) and math.isnan(raw):
            continue
        s = str(raw).strip()
        if s in _VALID_LABELS:
            out.iat[i] = s
    return out


def invert_signals(signals: pd.Series) -> pd.Series:
    """Swap Buy <-> Short while preserving None.

    This is the [I] direction tag from spec §18.
    """
    sig = normalize_signal_series(signals)
    return sig.map({_BUY: _SHORT, _SHORT: _BUY, _NONE: _NONE})


def combine_consensus_signals(members: Iterable[pd.Series]) -> pd.Series:
    """Apply spec §18 unanimity consensus across N primary signal series.

    Inputs must share the same index. The result is indexed identically.

    Per-day rules:
      - All non-None signals agree -> consensus is that signal.
      - Any non-None signals disagree -> consensus is None.
      - All signals are None -> consensus is None.
    """
    series_list: List[pd.Series] = [normalize_signal_series(s) for s in members]
    if not series_list:
        return pd.Series(dtype=object)
    idx = series_list[0].index
    mapper = {_BUY: 1, _SHORT: -1, _NONE: 0}
    columns = [s.map(lambda x: mapper.get(x, 0)).astype(np.int8).to_numpy() for s in series_list]
    arr = np.column_stack(columns) if len(columns) > 1 else columns[0].reshape(-1, 1)
    cnt = (arr != 0).sum(axis=1)
    ssum = arr.sum(axis=1)
    out = np.where(cnt == 0, _NONE,
           np.where(ssum == cnt, _BUY,
            np.where(ssum == -cnt, _SHORT, _NONE)))
    return pd.Series(out, index=idx, dtype=object)


def _captures_from_signals_decimal(signals: pd.Series, returns: pd.Series) -> pd.Series:
    """Spec §13: Buy -> ret*100, Short -> -ret*100, None -> 0.

    `returns` are decimal; output is percent points.
    """
    sig = normalize_signal_series(signals)
    ret = returns.reindex(sig.index).astype(float)
    out = pd.Series(0.0, index=sig.index, dtype=float)
    buy_mask = sig.eq(_BUY).to_numpy()
    short_mask = sig.eq(_SHORT).to_numpy()
    ret_arr = ret.to_numpy()
    out_arr = out.to_numpy().copy()
    out_arr[buy_mask] = ret_arr[buy_mask] * 100.0
    out_arr[short_mask] = -ret_arr[short_mask] * 100.0
    return pd.Series(out_arr, index=sig.index, dtype=float)


def score_captures(
    daily_capture: pd.Series,
    trigger_mask: pd.Series,
    *,
    risk_free_rate: float = 5.0,
    periods_per_year: int = 252,
    ddof: int = 1,
) -> CanonicalScore:
    """Score from a pre-computed daily_capture series and signal-based mask.

    `daily_capture` must already be in percent points (spec §13).
    `trigger_mask` is a boolean Series identifying which days are
    Buy/Short signal triggers; spec §12 / §15 say zero-capture
    trigger days still count.

    The cumulative capture series is a running sum across all days
    in `daily_capture.index`, with non-trigger / None days
    contributing 0 (spec §14).
    """
    if not isinstance(daily_capture, pd.Series):
        raise TypeError("daily_capture must be a pandas Series")
    if not isinstance(trigger_mask, pd.Series):
        raise TypeError("trigger_mask must be a pandas Series")
    mask = trigger_mask.reindex(daily_capture.index).fillna(False).astype(bool)

    # Enforce spec §14: only trigger days contribute to captures and to the
    # cumulative running sum. Any nonzero input on a non-trigger day is
    # zeroed out so total_capture and the final cumulative value agree.
    effective_daily_capture = daily_capture.fillna(0.0).astype(float).copy()
    effective_daily_capture[~mask] = 0.0
    cumulative = effective_daily_capture.cumsum()

    trigger_caps = effective_daily_capture[mask]
    trigger_days = int(mask.sum())

    if trigger_days == 0:
        return CanonicalScore(
            trigger_days=0, wins=0, losses=0, win_rate=0.0,
            avg_daily_capture=0.0,
            total_capture=float(cumulative.iloc[-1]) if len(cumulative) else 0.0,
            std_dev=0.0, sharpe=0.0,
            t_statistic=None, p_value=None,
            daily_capture=effective_daily_capture,
            cumulative_capture=cumulative,
        )

    wins = int((trigger_caps > 0).sum())
    losses = trigger_days - wins  # zero and negative captures are losses (spec §15)
    win_rate = (wins / trigger_days) * 100.0
    avg = float(trigger_caps.mean())
    total = float(trigger_caps.sum())

    if trigger_days > 1:
        std = float(np.std(trigger_caps.to_numpy(), ddof=ddof))
    else:
        std = 0.0

    sharpe = 0.0
    t_stat: Optional[float] = None
    p_val: Optional[float] = None
    if trigger_days > 1 and std > 0.0:
        annualized_return = avg * float(periods_per_year)
        annualized_std = std * math.sqrt(float(periods_per_year))
        sharpe = (annualized_return - float(risk_free_rate)) / annualized_std
        t_stat = avg / (std / math.sqrt(trigger_days))
        # Numerically stable: 2 * sf(|t|) == 2 * (1 - cdf(|t|)) for symmetric t.
        p_val = float(2.0 * stats.t.sf(abs(t_stat), df=trigger_days - 1))

    return CanonicalScore(
        trigger_days=trigger_days,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        avg_daily_capture=avg,
        total_capture=total,
        std_dev=std,
        sharpe=sharpe,
        t_statistic=t_stat,
        p_value=p_val,
        daily_capture=effective_daily_capture,
        cumulative_capture=cumulative,
    )


def score_signals(
    signals: pd.Series,
    returns: pd.Series,
    *,
    risk_free_rate: float = 5.0,
    periods_per_year: int = 252,
    ddof: int = 1,
) -> CanonicalScore:
    """End-to-end: signals + decimal returns -> CanonicalScore.

    See module docstring for unit conventions and spec rules.
    """
    sig = normalize_signal_series(signals)
    ret = returns.reindex(sig.index).astype(float).fillna(0.0)
    daily_capture = _captures_from_signals_decimal(sig, ret)
    trigger_mask = sig.isin([_BUY, _SHORT])
    return score_captures(
        daily_capture,
        trigger_mask,
        risk_free_rate=risk_free_rate,
        periods_per_year=periods_per_year,
        ddof=ddof,
    )


def metrics_to_legacy_dict(score: CanonicalScore) -> dict:
    """Project a CanonicalScore to the legacy display-dict shape used by
    onepass / impactsearch / stackbuilder / confluence today.

    Returns a dict with the round-on-display values and an N/A
    sentinel for missing t/p. The full-precision raw fields are also
    returned under `*_raw` keys. Engines free to map sub-keys to
    their preferred labels.
    """
    sig90 = "Yes" if (score.p_value is not None and score.p_value < 0.10) else "No"
    sig95 = "Yes" if (score.p_value is not None and score.p_value < 0.05) else "No"
    sig99 = "Yes" if (score.p_value is not None and score.p_value < 0.01) else "No"
    return {
        "Trigger Days": score.trigger_days,
        "Wins": score.wins,
        "Losses": score.losses,
        "Win Ratio (%)": round(score.win_rate, 2),
        "Std Dev (%)": round(score.std_dev, 4),
        "Sharpe Ratio": round(score.sharpe, 2),
        "Avg Daily Capture (%)": round(score.avg_daily_capture, 4),
        "Total Capture (%)": round(score.total_capture, 4),
        "t-Statistic": round(score.t_statistic, 4) if score.t_statistic is not None else "N/A",
        "p-Value": round(score.p_value, 4) if score.p_value is not None else "N/A",
        "Significant 90%": sig90,
        "Significant 95%": sig95,
        "Significant 99%": sig99,
        "Sharpe_raw": float(score.sharpe),
        "Avg_raw": float(score.avg_daily_capture),
        "Total_raw": float(score.total_capture),
        "p_raw": float(score.p_value) if score.p_value is not None else None,
    }
