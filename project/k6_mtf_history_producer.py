#!/usr/bin/env python3
"""K=6 MTF history producer.

Emits per-secondary ``k6_mtf_history_v1`` artifacts per the K=6 MTF
launch-path contract at
``md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md``.

For each of the 8 MVP secondaries (AAPL, AMZN, GOOGL, META, MSFT,
NVDA, SPY, TSLA), this producer:

  1. Resolves the StackBuilder K=6 stack via
     ``output/stackbuilder/<SEC>/selected_build.json`` ->
     ``selected_run_dir`` -> ``combo_k=6.json``. Exactly six members
     with ``[D]`` or ``[I]`` protocols are required.
  2. Loads each member's five per-timeframe signal libraries from
     ``signal_library/data/stable/<MEMBER>_stable_v1_0_0[_<INTERVAL>].pkl``
     for ``1d``, ``1wk``, ``1mo``, ``3mo``, ``1y``.
  3. Loads the secondary daily close via the local source-resolution
     chain (parquet -> csv -> ``cache/results/<SEC>_precomputed_results.pkl``
     fallback). No vendor / provider fetch.
  4. Applies ``[D]`` / ``[I]`` protocol to each member signal and
     combines the six protocol-adjusted signals using the locked
     lenient active-signal unanimity rule for every timeframe.
  5. Caps history at
     ``history_as_of_date = min(secondary close end date, six
     member 1d end dates)``.
  6. For the ``1d`` slot, evaluates each secondary daily date
     exactly (no forward-fill of member 1d signals). Missing
     exact-date member signals count neutral / UNAVAILABLE.
  7. For non-daily slots, builds each timeframe's K=6 combined
     stream at the timeframe's source dates, then forward-fills
     that combined stream onto the capped secondary daily calendar.
  8. Records descriptive participation-depth provenance
     (``active_buy_count`` + ``active_short_count`` + ``neutral_count``
     summing to 6) for every slot.
  9. Writes the artifact JSON under
     ``output/k6_mtf/<RUN_TIMESTAMP>/<SEC>/k6_mtf_history.json``.

Strict invariants:

  - Does NOT read or combine the secondary's own signal libraries.
  - Does NOT use the K-threshold combine
    (``research_artifacts.combine_member_signals``).
  - Does NOT forward-fill any member past ``history_as_of_date``.
  - Does NOT treat stale right-edge member signals as neutral; the
    cap removes those bars from the evaluated range entirely.
  - Does NOT fetch from any provider.

Importing this module has no side effects beyond a single
``logging.getLogger`` registration.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any, Callable, Dict, Iterable, List, Mapping, Optional,
    Sequence, Tuple,
)

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants and defaults
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "k6_mtf_history_v1"

DEFAULT_SECONDARIES: Tuple[str, ...] = (
    "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "SPY", "TSLA",
)

TIMEFRAME_SET: Tuple[str, ...] = ("1d", "1wk", "1mo", "3mo", "1y")
NON_DAILY_TIMEFRAMES: Tuple[str, ...] = ("1wk", "1mo", "3mo", "1y")

# Default project-relative input roots. Callers / the CLI override.
DEFAULT_STACKBUILDER_ROOT = "output/stackbuilder"
DEFAULT_STABLE_DIR = "signal_library/data/stable"
DEFAULT_CACHE_DIR = "cache/results"
DEFAULT_PRICE_CACHE_DIR = "price_cache/daily"
DEFAULT_OUTPUT_ROOT = "output/k6_mtf"

# Canonical signal vocabulary used in artifact snapshots / availability.
SIGNAL_BUY = "BUY"
SIGNAL_SHORT = "SHORT"
SIGNAL_NONE = "NONE"
SIGNAL_UNAVAILABLE = "UNAVAILABLE"

ACTIVE_SIGNALS = frozenset({SIGNAL_BUY, SIGNAL_SHORT})

# Member-library signal vocabulary (lowercase-insensitive). Anything
# outside this active set is treated as neutral per the contract.
_ACTIVE_BUY_TOKENS = frozenset({"buy", "BUY"})
_ACTIVE_SHORT_TOKENS = frozenset({"short", "SHORT"})


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class K6MtfHistoryError(Exception):
    """Producer-level failure for a single secondary. Callers can catch
    this to keep the multi-secondary run going while recording the
    failure for reporting."""


class K6StackResolutionError(K6MtfHistoryError):
    """Raised when the K=6 stack cannot be parsed from selected_build /
    combo_k=6 inputs."""


class SecondarySourceError(K6MtfHistoryError):
    """Raised when no usable local secondary close source can be
    resolved through the parquet/csv/PKL chain."""


class MemberLibraryError(K6MtfHistoryError):
    """Raised when a required per-(member, timeframe) signal library
    cannot be loaded."""


# ---------------------------------------------------------------------------
# Provenance loader (deferred import to keep top-level import side-effect free)
# ---------------------------------------------------------------------------


def _load_verified_pickle(path: Path) -> Any:
    """Load a pickle artifact via the project's provenance-safe loader.

    Returns the data on success. Raises ``MemberLibraryError`` if the
    artifact cannot be read or verification rejects it.
    """
    try:
        from provenance_manifest import load_verified_pickle_artifact
    except ImportError as exc:
        raise MemberLibraryError(
            f"provenance loader unavailable: {exc!r}"
        ) from exc
    try:
        data, vresult = load_verified_pickle_artifact(path)
    except Exception as exc:
        raise MemberLibraryError(
            f"load failed for {path}: {exc!r}"
        ) from exc
    if data is None:
        raise MemberLibraryError(
            f"verification mismatch for {path}: "
            f"{vresult.mismatches!r}"
        )
    if not (vresult.ok or vresult.legacy):
        raise MemberLibraryError(
            f"provenance rejected {path}: {vresult.mismatches!r}"
        )
    return data


# ---------------------------------------------------------------------------
# K=6 stack resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class K6Member:
    ticker: str
    protocol: str  # "D" or "I"
    original: str  # raw member string from combo_k=6.json


@dataclass(frozen=True)
class K6Stack:
    secondary: str
    selected_build_path: str
    selected_run_dir: str
    combo_k6_path: str
    members: Tuple[K6Member, ...]


def _parse_member_token(token: str) -> K6Member:
    if not isinstance(token, str):
        raise K6StackResolutionError(
            f"non-string K=6 member: {token!r}"
        )
    if "[D]" in token:
        return K6Member(
            ticker=token.split("[D]", 1)[0],
            protocol="D",
            original=token,
        )
    if "[I]" in token:
        return K6Member(
            ticker=token.split("[I]", 1)[0],
            protocol="I",
            original=token,
        )
    raise K6StackResolutionError(
        f"K=6 member missing [D]/[I] protocol marker: {token!r}"
    )


def resolve_k6_stack(
    secondary: str,
    *,
    stackbuilder_root: str = DEFAULT_STACKBUILDER_ROOT,
) -> K6Stack:
    """Parse the K=6 stack for ``secondary``. Fails closed on any
    missing/malformed input. Uses K=6 even if ``selected_build.json``
    points to another K."""
    selected_build_path = Path(stackbuilder_root) / secondary / "selected_build.json"
    if not selected_build_path.exists():
        raise K6StackResolutionError(
            f"selected_build.json missing for {secondary}: "
            f"{selected_build_path}"
        )
    try:
        sb = json.loads(selected_build_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise K6StackResolutionError(
            f"selected_build.json read error for {secondary}: {exc!r}"
        ) from exc
    selected_run_dir = sb.get("selected_run_dir")
    if not selected_run_dir or not isinstance(selected_run_dir, str):
        raise K6StackResolutionError(
            f"selected_run_dir missing/invalid in "
            f"{selected_build_path}"
        )
    run_dir = Path(selected_run_dir)
    if not run_dir.exists():
        raise K6StackResolutionError(
            f"selected_run_dir does not exist: {run_dir}"
        )
    combo_path = run_dir / "combo_k=6.json"
    if not combo_path.exists():
        raise K6StackResolutionError(
            f"combo_k=6.json missing for {secondary}: {combo_path}"
        )
    try:
        combo = json.loads(combo_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise K6StackResolutionError(
            f"combo_k=6.json read error for {secondary}: {exc!r}"
        ) from exc
    members_raw = (
        combo.get("Members")
        or combo.get("members")
        or combo.get("member_list")
    )
    if not isinstance(members_raw, list):
        raise K6StackResolutionError(
            f"K=6 members not a list in {combo_path}: "
            f"got type={type(members_raw).__name__}"
        )
    if len(members_raw) != 6:
        raise K6StackResolutionError(
            f"K=6 must have exactly six members; got "
            f"{len(members_raw)} in {combo_path}"
        )
    parsed = tuple(_parse_member_token(m) for m in members_raw)
    return K6Stack(
        secondary=secondary,
        selected_build_path=str(selected_build_path).replace("\\", "/"),
        selected_run_dir=str(run_dir).replace("\\", "/"),
        combo_k6_path=str(combo_path).replace("\\", "/"),
        members=parsed,
    )


# ---------------------------------------------------------------------------
# Member library loading
# ---------------------------------------------------------------------------


def _member_library_path(
    ticker: str, interval: str, *, stable_dir: str,
) -> Path:
    if interval == "1d":
        name = f"{ticker}_stable_v1_0_0.pkl"
    else:
        name = f"{ticker}_stable_v1_0_0_{interval}.pkl"
    return Path(stable_dir) / name


def load_member_library(
    ticker: str,
    interval: str,
    *,
    stable_dir: str = DEFAULT_STABLE_DIR,
) -> dict:
    """Load a single per-(member, timeframe) signal library and
    return a normalized dict with ``dates``, ``signals``, and the
    declared ``ticker`` / ``interval``. Raises ``MemberLibraryError``
    on any failure."""
    path = _member_library_path(ticker, interval, stable_dir=stable_dir)
    if not path.exists():
        raise MemberLibraryError(
            f"member library missing: {path}"
        )
    data = _load_verified_pickle(path)
    if not isinstance(data, dict):
        raise MemberLibraryError(
            f"member library {path} is not a dict (got "
            f"{type(data).__name__})"
        )
    dates = data.get("dates") or data.get("date_index")
    signals = data.get("signals") or data.get("primary_signals")
    if dates is None or signals is None:
        raise MemberLibraryError(
            f"member library {path} missing dates/signals fields"
        )
    if len(dates) != len(signals):
        raise MemberLibraryError(
            f"member library {path} dates/signals length mismatch: "
            f"{len(dates)} vs {len(signals)}"
        )
    try:
        idx = pd.DatetimeIndex(dates)
    except Exception as exc:
        raise MemberLibraryError(
            f"member library {path} dates not DatetimeIndex-coercible: "
            f"{exc!r}"
        ) from exc
    if hasattr(idx, "tz") and idx.tz is not None:
        idx = idx.tz_localize(None)
    if data.get("ticker") and data.get("ticker") != ticker:
        raise MemberLibraryError(
            f"member library {path} ticker mismatch: "
            f"library={data.get('ticker')!r} expected={ticker!r}"
        )
    if data.get("interval") and data.get("interval") != interval:
        raise MemberLibraryError(
            f"member library {path} interval mismatch: "
            f"library={data.get('interval')!r} expected={interval!r}"
        )
    return {
        "ticker": ticker,
        "interval": interval,
        "path": str(path).replace("\\", "/"),
        "dates": idx,
        "signals": list(signals),
    }


# ---------------------------------------------------------------------------
# Secondary close source resolution
# ---------------------------------------------------------------------------


def load_secondary_close(
    secondary: str,
    *,
    price_cache_dir: str = DEFAULT_PRICE_CACHE_DIR,
    cache_dir: str = DEFAULT_CACHE_DIR,
) -> Tuple[pd.Series, str, str]:
    """Resolve the secondary's daily close series locally.

    Returns a tuple ``(close_series, source_path, source_kind)`` where
    ``source_kind`` is one of ``parquet``, ``csv``, ``pkl_fallback``.

    Source resolution order: ``price_cache/daily/<SEC>.parquet`` ->
    ``price_cache/daily/<SEC>.csv`` ->
    ``cache/results/<SEC>_precomputed_results.pkl``. Raises
    ``SecondarySourceError`` if none of the three sources is usable.
    No network fetch.
    """
    parquet_path = Path(price_cache_dir) / f"{secondary}.parquet"
    csv_path = Path(price_cache_dir) / f"{secondary}.csv"
    pkl_path = Path(cache_dir) / f"{secondary}_precomputed_results.pkl"

    series: Optional[pd.Series] = None
    chosen_path: Optional[str] = None
    chosen_kind: Optional[str] = None

    if parquet_path.exists():
        try:
            df = pd.read_parquet(parquet_path)
            series = _extract_close_from_frame(df)
            chosen_path, chosen_kind = (
                str(parquet_path).replace("\\", "/"), "parquet",
            )
        except Exception as exc:
            logger.warning(
                f"{secondary}: parquet read failed at {parquet_path}: "
                f"{exc!r}"
            )
            series = None
    if series is None and csv_path.exists():
        try:
            df = pd.read_csv(csv_path)
            series = _extract_close_from_frame(df)
            chosen_path, chosen_kind = (
                str(csv_path).replace("\\", "/"), "csv",
            )
        except Exception as exc:
            logger.warning(
                f"{secondary}: csv read failed at {csv_path}: "
                f"{exc!r}"
            )
            series = None
    if series is None and pkl_path.exists():
        try:
            data = _load_verified_pickle(pkl_path)
            pre = data.get("preprocessed_data") if hasattr(
                data, "get",
            ) else None
            if pre is None or "Close" not in list(pre.columns):
                raise SecondarySourceError(
                    f"{secondary}: PKL fallback missing "
                    f"preprocessed_data/Close at {pkl_path}"
                )
            s = pre[["Close"]].copy()
            if hasattr(s.index, "tz") and s.index.tz is not None:
                s.index = s.index.tz_localize(None)
            else:
                s.index = pd.to_datetime(s.index).tz_localize(None)
            s = s.sort_index()
            if s.index.has_duplicates:
                s = s[~s.index.duplicated(keep="last")]
            series = s["Close"].astype(np.float64)
            chosen_path, chosen_kind = (
                str(pkl_path).replace("\\", "/"), "pkl_fallback",
            )
        except Exception as exc:
            logger.warning(
                f"{secondary}: PKL fallback failed: {exc!r}"
            )
            series = None

    if series is None or len(series) == 0:
        raise SecondarySourceError(
            f"{secondary}: no usable local secondary close source. "
            f"Tried parquet={parquet_path.exists()}, "
            f"csv={csv_path.exists()}, pkl={pkl_path.exists()}"
        )
    series.name = "Close"
    return series, chosen_path or "", chosen_kind or ""


def _extract_close_from_frame(df: pd.DataFrame) -> pd.Series:
    """Pull a tz-naive sorted float64 Close series out of a parquet/csv
    DataFrame that carries a Date/Close pair."""
    cols = list(df.columns)
    date_col = None
    for candidate in ("Date", "date", "Timestamp", "timestamp"):
        if candidate in cols:
            date_col = candidate
            break
    if date_col is None:
        if "Close" in cols:
            idx = pd.DatetimeIndex(df.index)
            close = df["Close"].astype(np.float64)
        else:
            raise ValueError(
                f"frame missing Date/Close columns: {cols!r}"
            )
    else:
        if "Close" not in cols:
            raise ValueError(
                f"frame missing Close column: {cols!r}"
            )
        idx = pd.DatetimeIndex(pd.to_datetime(df[date_col]))
        close = df["Close"].astype(np.float64)
    if hasattr(idx, "tz") and idx.tz is not None:
        idx = idx.tz_localize(None)
    s = pd.Series(close.to_numpy(dtype=np.float64), index=idx, name="Close")
    s = s.sort_index()
    if s.index.has_duplicates:
        s = s[~s.index.duplicated(keep="last")]
    return s


# ---------------------------------------------------------------------------
# Protocol + lenient combine
# ---------------------------------------------------------------------------


def _normalize_signal(value: Any) -> str:
    """Normalize a raw member signal into the canonical four-value
    vocabulary used in the artifact. Any unrecognized value is treated
    as neutral per the contract; ``UNAVAILABLE`` is reserved for slots
    where the producer explicitly knows the member's value is missing."""
    if value is None:
        return SIGNAL_NONE
    if isinstance(value, (int, np.integer)):
        if value == 1:
            return SIGNAL_BUY
        if value == -1:
            return SIGNAL_SHORT
        return SIGNAL_NONE
    if isinstance(value, float) and np.isnan(value):
        return SIGNAL_NONE
    text = str(value).strip()
    if not text:
        return SIGNAL_NONE
    lower = text.lower()
    if lower in _ACTIVE_BUY_TOKENS:
        return SIGNAL_BUY
    if lower in _ACTIVE_SHORT_TOKENS:
        return SIGNAL_SHORT
    if lower in ("unavailable",):
        return SIGNAL_UNAVAILABLE
    return SIGNAL_NONE


def apply_protocol(signal: str, protocol: str) -> str:
    """Apply ``[D]`` / ``[I]`` to a canonical signal. ``[D]`` preserves
    BUY / SHORT, ``[I]`` swaps BUY <-> SHORT. Neutral values
    (``NONE`` / ``UNAVAILABLE``) pass through unchanged."""
    if protocol == "D":
        return signal
    if protocol == "I":
        if signal == SIGNAL_BUY:
            return SIGNAL_SHORT
        if signal == SIGNAL_SHORT:
            return SIGNAL_BUY
        return signal
    raise ValueError(f"unknown protocol {protocol!r}; expected D or I")


def combine_six(canonical_six: Sequence[str]) -> Tuple[str, int, int, int]:
    """Lenient active-signal unanimity combine over six protocol-
    adjusted signals.

    Returns ``(combined, active_buy_count, active_short_count,
    neutral_count)`` with the invariant
    ``active_buy_count + active_short_count + neutral_count == 6``.

    The combined signal is:
      - BUY  if active_buy_count > 0 and active_short_count == 0
      - SHORT if active_short_count > 0 and active_buy_count == 0
      - NONE if conflict (both > 0) or all neutral (both == 0)
    """
    if len(canonical_six) != 6:
        raise ValueError(
            f"combine_six requires 6 signals; got {len(canonical_six)}"
        )
    active_buy = sum(1 for s in canonical_six if s == SIGNAL_BUY)
    active_short = sum(1 for s in canonical_six if s == SIGNAL_SHORT)
    neutral = 6 - active_buy - active_short
    if active_buy > 0 and active_short == 0:
        combined = SIGNAL_BUY
    elif active_short > 0 and active_buy == 0:
        combined = SIGNAL_SHORT
    else:
        combined = SIGNAL_NONE
    return combined, active_buy, active_short, neutral


# ---------------------------------------------------------------------------
# As-of cap
# ---------------------------------------------------------------------------


def compute_history_as_of(
    secondary_close_end: pd.Timestamp,
    member_1d_ends: Sequence[pd.Timestamp],
) -> pd.Timestamp:
    """``history_as_of_date = min(secondary_close_end, all six
    member 1d end dates)``."""
    if len(member_1d_ends) == 0:
        raise ValueError("member_1d_ends is empty")
    candidates = [pd.Timestamp(secondary_close_end)] + [
        pd.Timestamp(d) for d in member_1d_ends
    ]
    return min(candidates)


# ---------------------------------------------------------------------------
# Per-secondary build
# ---------------------------------------------------------------------------


def _build_combined_series_for_timeframe(
    timeframe: str,
    stack: K6Stack,
    member_libs_by_tf: Mapping[str, Dict[str, dict]],
) -> Tuple[pd.DatetimeIndex, List[str], List[Tuple[int, int, int]]]:
    """Build the K=6 combined signal stream on the union of all six
    members' source dates for ``timeframe``.

    Returns ``(union_dates, combined_signals, counts_tuples)`` aligned
    1:1. ``counts_tuples`` is a list of ``(buy_count, short_count,
    neutral_count)``.
    """
    libs = member_libs_by_tf[timeframe]
    member_indexed: Dict[str, pd.Series] = {}
    union_idx = pd.DatetimeIndex([])
    for member in stack.members:
        lib = libs[member.ticker]
        series = pd.Series(
            lib["signals"], index=lib["dates"],
        )
        # Drop duplicate dates if any (keep last).
        if series.index.has_duplicates:
            series = series[~series.index.duplicated(keep="last")]
        member_indexed[member.ticker] = series
        union_idx = union_idx.union(series.index)
    union_idx = pd.DatetimeIndex(sorted(union_idx))

    combined: List[str] = []
    counts: List[Tuple[int, int, int]] = []
    for date in union_idx:
        six = []
        for member in stack.members:
            series = member_indexed[member.ticker]
            if date in series.index:
                raw = series.loc[date]
                # If duplicate dropped but still multi, take last.
                if isinstance(raw, pd.Series):
                    raw = raw.iloc[-1]
                normalized = _normalize_signal(raw)
            else:
                normalized = SIGNAL_UNAVAILABLE
            adjusted = apply_protocol(normalized, member.protocol)
            six.append(adjusted)
        c, b, s, n = combine_six(six)
        combined.append(c)
        counts.append((b, s, n))
    return union_idx, combined, counts


def _forward_fill_combined_stream(
    source_dates: pd.DatetimeIndex,
    source_signals: Sequence[str],
    source_counts: Sequence[Tuple[int, int, int]],
    target_calendar: pd.DatetimeIndex,
) -> List[Dict[str, Any]]:
    """Forward-fill a combined signal stream onto a target calendar.

    For each target date, find the most recent source date <= target.
    Returns a list of dicts with keys ``signal``, ``source_date``,
    ``buy_count``, ``short_count``, ``neutral_count``, ``status``.

    If no source date <= target exists, returns a sentinel block with
    signal=NONE, source_date=None, counts=(0,0,6), status=unavailable.
    """
    out: List[Dict[str, Any]] = []
    if len(source_dates) == 0:
        for _ in target_calendar:
            out.append({
                "signal": SIGNAL_NONE,
                "source_date": None,
                "buy_count": 0,
                "short_count": 0,
                "neutral_count": 6,
                "status": "unavailable",
            })
        return out
    # Position-based lookup: for each target date, find the rightmost
    # source date <= target.
    sd = source_dates
    for target in target_calendar:
        pos = sd.searchsorted(target, side="right") - 1
        if pos < 0:
            out.append({
                "signal": SIGNAL_NONE,
                "source_date": None,
                "buy_count": 0,
                "short_count": 0,
                "neutral_count": 6,
                "status": "unavailable",
            })
            continue
        src_date = sd[pos]
        b, s, n = source_counts[pos]
        out.append({
            "signal": source_signals[pos],
            "source_date": pd.Timestamp(src_date),
            "buy_count": b,
            "short_count": s,
            "neutral_count": n,
            "status": (
                "computed" if pd.Timestamp(src_date) == pd.Timestamp(target)
                else "forward_filled"
            ),
        })
    return out


def _build_one_d_slot_for_calendar(
    stack: K6Stack,
    member_libs_one_d: Dict[str, dict],
    target_calendar: pd.DatetimeIndex,
) -> List[Dict[str, Any]]:
    """Build the 1d slot for each target date. The 1d slot does NOT
    forward-fill member 1d signals: missing exact-date member 1d
    signals count UNAVAILABLE (neutral) for that slot."""
    indexed: Dict[str, pd.Series] = {}
    for member in stack.members:
        lib = member_libs_one_d[member.ticker]
        series = pd.Series(lib["signals"], index=lib["dates"])
        if series.index.has_duplicates:
            series = series[~series.index.duplicated(keep="last")]
        indexed[member.ticker] = series

    out: List[Dict[str, Any]] = []
    for date in target_calendar:
        six = []
        any_missing = False
        all_missing = True
        for member in stack.members:
            series = indexed[member.ticker]
            if date in series.index:
                raw = series.loc[date]
                if isinstance(raw, pd.Series):
                    raw = raw.iloc[-1]
                normalized = _normalize_signal(raw)
                all_missing = False
            else:
                normalized = SIGNAL_UNAVAILABLE
                any_missing = True
            adjusted = apply_protocol(normalized, member.protocol)
            six.append(adjusted)
        c, b, s, n = combine_six(six)
        if all_missing:
            status = "unavailable"
            source_date = None
        elif any_missing:
            status = "computed"  # combined from partial info
            source_date = pd.Timestamp(date)
        else:
            status = "computed"
            source_date = pd.Timestamp(date)
        out.append({
            "signal": c,
            "source_date": source_date,
            "buy_count": b,
            "short_count": s,
            "neutral_count": n,
            "status": status,
        })
    return out


def build_history_for_secondary(
    secondary: str,
    *,
    run_id: str,
    generated_at_utc: Optional[str] = None,
    stackbuilder_root: str = DEFAULT_STACKBUILDER_ROOT,
    stable_dir: str = DEFAULT_STABLE_DIR,
    cache_dir: str = DEFAULT_CACHE_DIR,
    price_cache_dir: str = DEFAULT_PRICE_CACHE_DIR,
) -> dict:
    """Build one secondary's ``k6_mtf_history_v1`` artifact dict in
    memory.

    Raises ``K6MtfHistoryError`` (or a subclass) if the secondary
    cannot be built; callers can catch this to keep the run going.
    """
    stack = resolve_k6_stack(
        secondary, stackbuilder_root=stackbuilder_root,
    )

    # Load all 30 (member, timeframe) libraries.
    member_libs_by_tf: Dict[str, Dict[str, dict]] = {
        tf: {} for tf in TIMEFRAME_SET
    }
    for member in stack.members:
        for tf in TIMEFRAME_SET:
            lib = load_member_library(
                member.ticker, tf, stable_dir=stable_dir,
            )
            member_libs_by_tf[tf][member.ticker] = lib

    # Load secondary close.
    sec_series, sec_path, sec_kind = load_secondary_close(
        secondary,
        price_cache_dir=price_cache_dir,
        cache_dir=cache_dir,
    )

    # Compute history_as_of_date.
    member_1d_ends = [
        pd.Timestamp(member_libs_by_tf["1d"][m.ticker]["dates"][-1])
        for m in stack.members
    ]
    secondary_close_end = pd.Timestamp(sec_series.index[-1])
    as_of = compute_history_as_of(secondary_close_end, member_1d_ends)

    # Capped secondary calendar.
    sec_capped = sec_series[sec_series.index <= as_of].sort_index()

    # Non-daily combined streams.
    combined_streams: Dict[str, Tuple[pd.DatetimeIndex, List[str], List[Tuple[int, int, int]]]] = {}
    for tf in NON_DAILY_TIMEFRAMES:
        union_idx, combined_sigs, counts = (
            _build_combined_series_for_timeframe(
                tf, stack, member_libs_by_tf,
            )
        )
        # Truncate to as_of: no source bar after history_as_of_date
        # may participate in forward-fill.
        if len(union_idx):
            mask = union_idx <= as_of
            union_idx = union_idx[mask]
            combined_sigs = [
                s for s, keep in zip(combined_sigs, mask) if keep
            ]
            counts = [
                c for c, keep in zip(counts, mask) if keep
            ]
        combined_streams[tf] = (union_idx, combined_sigs, counts)

    # 1d slot on the capped secondary calendar.
    one_d_blocks = _build_one_d_slot_for_calendar(
        stack, member_libs_by_tf["1d"], sec_capped.index,
    )

    # Non-daily slots forward-filled on capped calendar.
    non_daily_blocks: Dict[str, List[Dict[str, Any]]] = {}
    for tf in NON_DAILY_TIMEFRAMES:
        idx, sigs, counts = combined_streams[tf]
        non_daily_blocks[tf] = _forward_fill_combined_stream(
            idx, sigs, counts, sec_capped.index,
        )

    # Assemble per-bar list.
    bars: List[Dict[str, Any]] = []
    for i, bar_date in enumerate(sec_capped.index):
        snapshot: Dict[str, str] = {}
        source_dates: Dict[str, Optional[str]] = {}
        availability: Dict[str, Dict[str, Any]] = {}
        one_d = one_d_blocks[i]
        snapshot["1d"] = one_d["signal"]
        source_dates["1d"] = (
            _fmt_date(one_d["source_date"])
            if one_d["source_date"] is not None else None
        )
        availability["1d"] = {
            "status": one_d["status"],
            "active_buy_count": one_d["buy_count"],
            "active_short_count": one_d["short_count"],
            "neutral_count": one_d["neutral_count"],
        }
        for tf in NON_DAILY_TIMEFRAMES:
            block = non_daily_blocks[tf][i]
            snapshot[tf] = block["signal"]
            source_dates[tf] = (
                _fmt_date(block["source_date"])
                if block["source_date"] is not None else None
            )
            availability[tf] = {
                "status": block["status"],
                "active_buy_count": block["buy_count"],
                "active_short_count": block["short_count"],
                "neutral_count": block["neutral_count"],
            }
        bars.append({
            "date_utc": _fmt_date(bar_date),
            "secondary_close": float(sec_capped.iloc[i]),
            "snapshot": snapshot,
            "source_dates": source_dates,
            "availability": availability,
        })

    # source_paths object: secondary, per-member 1d PKL fallback path,
    # per-(member, timeframe) library paths.
    source_paths: Dict[str, Any] = {
        "secondary_close": {
            "path": sec_path,
            "kind": sec_kind,
            "end_date": _fmt_date(secondary_close_end),
        },
        "secondary_close_end_date": _fmt_date(secondary_close_end),
        "history_as_of_date": _fmt_date(as_of),
        "member_1d_end_dates": {
            m.ticker: _fmt_date(
                member_libs_by_tf["1d"][m.ticker]["dates"][-1]
            )
            for m in stack.members
        },
        "members": {
            m.ticker: {
                tf: member_libs_by_tf[tf][m.ticker]["path"]
                for tf in TIMEFRAME_SET
            }
            for m in stack.members
        },
        "as_of_truncation": {
            "secondary_close_end_date": _fmt_date(secondary_close_end),
            "member_1d_end_dates": {
                m.ticker: _fmt_date(
                    member_libs_by_tf["1d"][m.ticker]["dates"][-1]
                )
                for m in stack.members
            },
            "selected_history_as_of_date": _fmt_date(as_of),
            "trimmed_secondary_bars": int(
                (sec_series.index > as_of).sum()
            ),
        },
    }

    if generated_at_utc is None:
        generated_at_utc = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(),
        )

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc,
        "run_id": run_id,
        "secondary": secondary,
        "history_as_of_date": _fmt_date(as_of),
        "source_paths": source_paths,
        "k6_stack": {
            "selected_build_path": stack.selected_build_path,
            "selected_run_dir": stack.selected_run_dir,
            "combo_k6_path": stack.combo_k6_path,
            "members": [
                {"ticker": m.ticker, "protocol": m.protocol}
                for m in stack.members
            ],
        },
        "timeframe_set": list(TIMEFRAME_SET),
        "bars": bars,
        "issues": [],
    }
    return artifact


def _fmt_date(value: Any) -> str:
    """Format a date as YYYY-MM-DD UTC date string."""
    if isinstance(value, str):
        return value[:10]
    ts = pd.Timestamp(value)
    return ts.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Write artifact
# ---------------------------------------------------------------------------


def write_history_artifact(
    artifact: Mapping[str, Any],
    *,
    output_root: str = DEFAULT_OUTPUT_ROOT,
    run_id: Optional[str] = None,
) -> Path:
    """Write the artifact dict to
    ``<output_root>/<run_id>/<secondary>/k6_mtf_history.json``.

    Returns the written path.
    """
    if run_id is None:
        run_id = artifact["run_id"]
    secondary = artifact["secondary"]
    target_dir = Path(output_root) / run_id / secondary
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "k6_mtf_history.json"
    target_path.write_text(
        json.dumps(artifact, indent=2, sort_keys=False, default=str),
        encoding="utf-8",
    )
    return target_path


# ---------------------------------------------------------------------------
# Multi-secondary runner / CLI
# ---------------------------------------------------------------------------


def _make_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def run(
    secondaries: Sequence[str],
    *,
    run_id: Optional[str] = None,
    output_root: str = DEFAULT_OUTPUT_ROOT,
    stackbuilder_root: str = DEFAULT_STACKBUILDER_ROOT,
    stable_dir: str = DEFAULT_STABLE_DIR,
    cache_dir: str = DEFAULT_CACHE_DIR,
    price_cache_dir: str = DEFAULT_PRICE_CACHE_DIR,
) -> dict:
    """Run the producer across multiple secondaries.

    Returns a summary dict with per-secondary status. Continues across
    secondary-level failures; raises only on unrecoverable
    programmer errors.
    """
    if run_id is None:
        run_id = _make_run_id()
    generated_at_utc = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(),
    )
    results: Dict[str, Dict[str, Any]] = {}
    written_paths: List[str] = []
    failures: List[Dict[str, Any]] = []
    for secondary in secondaries:
        try:
            artifact = build_history_for_secondary(
                secondary,
                run_id=run_id,
                generated_at_utc=generated_at_utc,
                stackbuilder_root=stackbuilder_root,
                stable_dir=stable_dir,
                cache_dir=cache_dir,
                price_cache_dir=price_cache_dir,
            )
            path = write_history_artifact(
                artifact, output_root=output_root, run_id=run_id,
            )
            results[secondary] = {
                "status": "ok",
                "artifact_path": str(path).replace("\\", "/"),
                "history_as_of_date": artifact["history_as_of_date"],
                "bar_count": len(artifact["bars"]),
            }
            written_paths.append(str(path).replace("\\", "/"))
            logger.info(
                f"{secondary}: wrote {path} "
                f"(bars={len(artifact['bars'])} "
                f"as_of={artifact['history_as_of_date']})"
            )
        except K6MtfHistoryError as exc:
            results[secondary] = {
                "status": "failed",
                "error_class": type(exc).__name__,
                "error_message": str(exc),
            }
            failures.append({
                "secondary": secondary,
                "error_class": type(exc).__name__,
                "error_message": str(exc),
            })
            logger.error(f"{secondary}: failed - {exc}")
    summary = {
        "run_id": run_id,
        "output_root": output_root,
        "generated_at_utc": generated_at_utc,
        "secondaries_requested": list(secondaries),
        "results": results,
        "written_paths": written_paths,
        "failures": failures,
    }
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "K=6 MTF history producer. Emits k6_mtf_history_v1 "
            "artifacts per secondary."
        ),
    )
    parser.add_argument(
        "--secondaries",
        default=",".join(DEFAULT_SECONDARIES),
        help=(
            "Comma-separated secondaries; defaults to the 8 MVP "
            "secondaries."
        ),
    )
    parser.add_argument(
        "--output-root", default=DEFAULT_OUTPUT_ROOT,
    )
    parser.add_argument(
        "--stackbuilder-root", default=DEFAULT_STACKBUILDER_ROOT,
    )
    parser.add_argument(
        "--stable-dir", default=DEFAULT_STABLE_DIR,
    )
    parser.add_argument(
        "--cache-dir", default=DEFAULT_CACHE_DIR,
    )
    parser.add_argument(
        "--price-cache-dir", default=DEFAULT_PRICE_CACHE_DIR,
    )
    parser.add_argument(
        "--run-id", default=None,
        help=(
            "Override the UTC timestamp run id. Default: generated "
            "as %%Y%%m%%dT%%H%%M%%SZ at runtime."
        ),
    )
    args = parser.parse_args(argv)

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )

    secondaries = [
        s.strip() for s in args.secondaries.split(",") if s.strip()
    ]
    summary = run(
        secondaries,
        run_id=args.run_id,
        output_root=args.output_root,
        stackbuilder_root=args.stackbuilder_root,
        stable_dir=args.stable_dir,
        cache_dir=args.cache_dir,
        price_cache_dir=args.price_cache_dir,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not summary["failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
