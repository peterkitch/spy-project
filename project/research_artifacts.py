"""Phase 6B: canonical day-by-day research artifacts.

Saved ranking tables (ImpactSearch's per-row ``Total Capture (%)`` /
``Sharpe Ratio`` / ``Trigger Days`` / etc.) are point-in-time
summaries. They do NOT include the full day-by-day path that produced
those numbers. The Phase 6A cockpit had to reconstruct charts from
the separate stable signal library + Spymaster cache, which can drift
out of date alignment with the ranked output.

This module introduces a saved-on-disk artifact format that captures
both the daily path (date / signal / target close / target return /
daily capture / cumulative capture / trigger flag) AND the headline
summary (Total Capture, Avg Daily Capture, Sharpe Ratio, Wins,
Losses, p-value, 95% Confidence) in a single JSON blob per
(target, signal source, run_id) tuple.

  Phase 6B-1: ImpactSearch single-signal slice (engine="impactsearch").
  Phase 6B-2: StackBuilder day-by-day stack capture
              (engine="stackbuilder") + catalogue_index.json.
  Phase 6B-3: Confluence day-by-day 7-tier path
              (engine="confluence").
  TODO Phase 6B-4: Traffic Flow per-day pressure artifacts
                   (engine="trafficflow").
  TODO Phase 6C: public catalogue UX + caching model.

All read paths are strictly read-only and offline. The single write
helper persists JSON to ``output/research_artifacts/<engine>/<TARGET>/``.
No network access. No yfinance.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

# Format identifier baked into every saved artifact. Future schema
# evolution should bump this rather than mutate fields silently.
ARTIFACT_VERSION = "research_day_v1"


# Default T-1 persistence skip. Constant rather than lazy-resolving
# from ``impactsearch.PERSIST_SKIP_BARS`` so importing this module
# never triggers Dash / Spymaster / TrafficFlow / Confluence import
# chains. Callers that need a different value pass
# ``persist_skip_bars`` explicitly to the build helpers. Phase 6B-2
# pinned the constant to 1 to match the documented ImpactSearch
# policy without an at-import-time dependency.
DEFAULT_PERSIST_SKIP_BARS = 1


# Filename-safe ticker normalization. Mirrors the small helper in
# ``phase6_research_preview._normalize_ticker_for_filename`` so this
# module has no dependency on the preview.
_FILENAME_SAFE_RX = re.compile(r"[^A-Za-z0-9_\-\.]")


def _normalize_ticker_for_filename(ticker: Optional[str]) -> str:
    if not ticker:
        return ""
    s = str(ticker).strip().upper()
    if not s:
        return ""
    # ^GSPC -> _GSPC, BTC-USD stays, foo/bar -> foo_bar.
    s = s.replace("^", "_")
    s = _FILENAME_SAFE_RX.sub("_", s)
    return s


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_DEFAULT_BASE = Path("output") / "research_artifacts"


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def artifact_path_for_impactsearch(
    target_ticker: str,
    signal_source: str,
    run_id: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Return the canonical local artifact path for an ImpactSearch
    (target, signal_source) pair. ``run_id`` lets multiple runs per
    pair coexist without overwrite; when omitted, the path is the
    "default" artifact for that pair.

    Returns None if either ticker normalizes to empty (handles None /
    whitespace / pathological symbols).
    """
    safe_target = _normalize_ticker_for_filename(target_ticker)
    safe_source = _normalize_ticker_for_filename(signal_source)
    if not safe_target or not safe_source:
        return None
    base = (
        Path(base_dir) if base_dir is not None
        else _project_dir() / _DEFAULT_BASE
    )
    folder = base / "impactsearch" / safe_target
    suffix = f"__{run_id}" if run_id else ""
    return folder / f"{safe_source}{suffix}.research_day.json"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


@dataclass
class ResearchDayArtifact:
    """In-memory representation of a research_day_v1 artifact. The
    write helper serializes this dataclass to JSON; the read helper
    rehydrates the same shape.

    ``signal_source`` carries the per-pair source for the
    ``impactsearch`` engine. For the ``stackbuilder`` engine the
    stack-specific fields ``K``, ``members``, ``protocol_per_member``
    are populated and ``signal_source`` stays empty.
    """

    artifact_version: str
    engine: str
    target_ticker: str
    signal_source: str
    run_id: Optional[str]
    metric_basis: str
    persist_skip_bars: int
    generated_at: str
    summary: dict
    daily: list[dict] = field(default_factory=list)
    # Stack-only fields. Populated by ``build_stackbuilder_day_artifact``;
    # left at their defaults for ``impactsearch`` artifacts.
    K: Optional[int] = None
    members: list[str] = field(default_factory=list)
    protocol_per_member: dict = field(default_factory=dict)
    # Confluence-only fields. Populated by
    # ``build_confluence_day_artifact``; left at defaults for
    # ``impactsearch`` / ``stackbuilder`` artifacts.
    timeframes: list[str] = field(default_factory=list)
    min_active: Optional[int] = None


def _to_iso_date(value: Any) -> str:
    """Best-effort YYYY-MM-DD string for a date-like value. Returns
    empty string if the value cannot be coerced."""
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return ""
    if pd.isna(ts):
        return ""
    return ts.strftime("%Y-%m-%d")


def _safe_float(value: Any) -> Optional[float]:
    """Return a finite Python float, or None for nan/inf/non-numeric."""
    try:
        f = float(value)
    except Exception:
        return None
    if not math.isfinite(f):
        return None
    return f


def _safe_int(value: Any) -> Optional[int]:
    f = _safe_float(value)
    if f is None:
        return None
    return int(round(f))


def build_impactsearch_day_artifact(
    target_ticker: str,
    signal_source: str,
    *,
    dates: Sequence[Any],
    signals: Sequence[Any],
    target_close: Sequence[float],
    persist_skip_bars: Optional[int] = None,
    metric_basis: str = "Close",
    run_id: Optional[str] = None,
    summary_overrides: Optional[Mapping[str, Any]] = None,
) -> ResearchDayArtifact:
    """Build the canonical day-by-day artifact for an ImpactSearch
    (target, signal_source) pair using ImpactSearch's daily-capture
    semantics:

      * Buy day -> daily_capture = +pct_change(target_close) * 100
      * Short day -> daily_capture = -pct_change(target_close) * 100
      * None / Cash / missing -> 0
      * trigger days = Buy or Short
      * T-1 persist skip applied to the trailing N bars before the
        cumulative sum (default = ``impactsearch.PERSIST_SKIP_BARS``,
        falling back to 1 when the engine import is not available)

    The summary block captures ``total_capture_pct``,
    ``avg_daily_capture_pct``, ``sharpe_ratio``, ``trigger_days``,
    ``wins``, ``losses``, ``p_value``, ``significant_95``. Numeric
    fields use ``ddof=1`` sample stats to match canonical_scoring.

    ``summary_overrides`` lets callers stamp the saved row's existing
    summary numbers (e.g. the Total Capture (%) ImpactSearch already
    persisted) onto the artifact so downstream code can compare the
    rebuilt summary against the engine's authoritative value.
    """
    if not isinstance(dates, (list, tuple, np.ndarray, pd.Series, pd.Index)):
        raise TypeError("dates must be a sequence")
    if not isinstance(signals, (list, tuple, np.ndarray, pd.Series)):
        raise TypeError("signals must be a sequence")
    if not isinstance(target_close, (list, tuple, np.ndarray, pd.Series)):
        raise TypeError("target_close must be a sequence")
    n = len(dates)
    if n != len(signals) or n != len(target_close):
        raise ValueError(
            "dates / signals / target_close must have equal length"
        )

    skip = (
        DEFAULT_PERSIST_SKIP_BARS if persist_skip_bars is None
        else int(persist_skip_bars)
    )

    # Normalize inputs. Use pandas for robust pct_change + nan handling.
    df = pd.DataFrame({
        "date": pd.to_datetime(list(dates), errors="coerce"),
        "signal": [str(s).strip() for s in signals],
        "target_close": pd.to_numeric(list(target_close), errors="coerce"),
    })
    df = df[df["date"].notna()].sort_values("date").reset_index(drop=True)
    df["target_return_pct"] = (
        df["target_close"].pct_change().fillna(0.0) * 100.0
    )
    sig_norm = df["signal"].str.lower()
    df["daily_capture_pct"] = 0.0
    df.loc[sig_norm.eq("buy"), "daily_capture_pct"] = (
        df.loc[sig_norm.eq("buy"), "target_return_pct"]
    )
    df.loc[sig_norm.eq("short"), "daily_capture_pct"] = (
        -df.loc[sig_norm.eq("short"), "target_return_pct"]
    )
    df["is_trigger_day"] = sig_norm.isin({"buy", "short"})

    # T-1 persistence skip: drop the trailing N bars before the
    # cumulative sum + summary stats.
    if skip and skip > 0 and len(df) > skip:
        df_trim = df.iloc[:-skip].copy()
    else:
        df_trim = df.copy()
    df_trim["cumulative_capture_pct"] = (
        df_trim["daily_capture_pct"].cumsum()
    )

    # Summary stats. ddof=1 to match canonical_scoring sample-std.
    trigger_mask = df_trim["is_trigger_day"]
    trigger_caps = df_trim.loc[trigger_mask, "daily_capture_pct"]
    n_trigger = int(trigger_mask.sum())
    if n_trigger > 0:
        total_capture_pct = float(trigger_caps.sum())
        avg_daily_capture_pct = float(trigger_caps.mean())
        wins = int((trigger_caps > 0).sum())
        losses = int((trigger_caps < 0).sum())
    else:
        total_capture_pct = 0.0
        avg_daily_capture_pct = 0.0
        wins = 0
        losses = 0
    if n_trigger > 1:
        std_dev = float(trigger_caps.std(ddof=1))
        sharpe_ratio = (
            avg_daily_capture_pct / std_dev if std_dev > 0
            else 0.0
        )
    else:
        sharpe_ratio = 0.0

    overrides = dict(summary_overrides or {})
    summary: dict = {
        "total_capture_pct": _safe_float(
            overrides.get("total_capture_pct", total_capture_pct),
        ),
        "avg_daily_capture_pct": _safe_float(
            overrides.get(
                "avg_daily_capture_pct", avg_daily_capture_pct,
            ),
        ),
        "sharpe_ratio": _safe_float(
            overrides.get("sharpe_ratio", sharpe_ratio),
        ),
        "trigger_days": _safe_int(
            overrides.get("trigger_days", n_trigger),
        ),
        "wins": _safe_int(overrides.get("wins", wins)),
        "losses": _safe_int(overrides.get("losses", losses)),
        "p_value": _safe_float(overrides.get("p_value")),
        "significant_95": (
            None if overrides.get("significant_95") is None
            else bool(overrides.get("significant_95"))
        ),
        # Rebuilt-from-rows fields for parity comparison even when the
        # caller stamps engine-authoritative overrides above.
        "rebuilt_total_capture_pct": total_capture_pct,
        "rebuilt_sharpe_ratio": sharpe_ratio,
        "rebuilt_trigger_days": n_trigger,
    }

    daily: list[dict] = []
    for _, row in df_trim.iterrows():
        daily.append({
            "date": _to_iso_date(row["date"]),
            "signal": str(row["signal"]),
            "target_close": _safe_float(row["target_close"]),
            "target_return_pct": _safe_float(row["target_return_pct"]),
            "daily_capture_pct": _safe_float(row["daily_capture_pct"]),
            "cumulative_capture_pct": _safe_float(
                row["cumulative_capture_pct"],
            ),
            "is_trigger_day": bool(row["is_trigger_day"]),
        })

    return ResearchDayArtifact(
        artifact_version=ARTIFACT_VERSION,
        engine="impactsearch",
        target_ticker=str(target_ticker).strip().upper(),
        signal_source=str(signal_source).strip().upper(),
        run_id=run_id,
        metric_basis=str(metric_basis or "Close"),
        persist_skip_bars=int(skip),
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        summary=summary,
        daily=daily,
    )


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def write_research_day_artifact(
    artifact: ResearchDayArtifact,
    path: Path,
) -> Path:
    """Persist ``artifact`` as JSON at ``path`` (creates parent dirs).
    Returns the resolved path."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact_version": artifact.artifact_version,
        "engine": artifact.engine,
        "target_ticker": artifact.target_ticker,
        "signal_source": artifact.signal_source,
        "run_id": artifact.run_id,
        "metric_basis": artifact.metric_basis,
        "persist_skip_bars": int(artifact.persist_skip_bars),
        "generated_at": artifact.generated_at,
        "summary": dict(artifact.summary or {}),
        "daily": list(artifact.daily or []),
    }
    # Stack-only fields: only persisted when populated.
    if artifact.K is not None:
        payload["K"] = int(artifact.K)
    if artifact.members:
        payload["members"] = list(artifact.members)
    if artifact.protocol_per_member:
        payload["protocol_per_member"] = dict(artifact.protocol_per_member)
    # Confluence-only fields: only persisted when populated.
    if artifact.timeframes:
        payload["timeframes"] = list(artifact.timeframes)
    if artifact.min_active is not None:
        payload["min_active"] = int(artifact.min_active)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    return out_path


def read_research_day_artifact(
    path: Path,
) -> Optional[ResearchDayArtifact]:
    """Read a saved ``research_day_v1`` artifact from ``path``.
    Returns None for missing / unreadable / wrong-version files. Never
    raises - callers fall back to the reconstructed path on None."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("artifact_version")
    if version != ARTIFACT_VERSION:
        return None
    try:
        K_val = payload.get("K")
        ma_val = payload.get("min_active")
        return ResearchDayArtifact(
            artifact_version=str(version),
            engine=str(payload.get("engine") or ""),
            target_ticker=str(payload.get("target_ticker") or ""),
            signal_source=str(payload.get("signal_source") or ""),
            run_id=payload.get("run_id"),
            metric_basis=str(payload.get("metric_basis") or "Close"),
            persist_skip_bars=int(payload.get("persist_skip_bars") or 0),
            generated_at=str(payload.get("generated_at") or ""),
            summary=dict(payload.get("summary") or {}),
            daily=list(payload.get("daily") or []),
            K=int(K_val) if K_val is not None else None,
            members=list(payload.get("members") or []),
            protocol_per_member=dict(
                payload.get("protocol_per_member") or {}
            ),
            timeframes=list(payload.get("timeframes") or []),
            min_active=int(ma_val) if ma_val is not None else None,
        )
    except Exception:
        return None


def summarize_research_day_artifact(
    artifact: ResearchDayArtifact,
) -> dict:
    """Return a small summary dict suitable for cockpit display.

    Keys: ``rows``, ``first_date``, ``last_date``,
    ``final_cumulative_capture_pct``, ``trigger_days``,
    ``total_capture_pct``, ``sharpe_ratio``, ``persist_skip_bars``,
    ``engine``, ``run_id``.
    """
    daily = artifact.daily or []
    rows = len(daily)
    first_date = daily[0].get("date") if rows else None
    last_date = daily[-1].get("date") if rows else None
    final_cum = (
        daily[-1].get("cumulative_capture_pct") if rows else None
    )
    return {
        "rows": rows,
        "first_date": first_date,
        "last_date": last_date,
        "final_cumulative_capture_pct": final_cum,
        "trigger_days": (artifact.summary or {}).get("trigger_days"),
        "total_capture_pct": (
            (artifact.summary or {}).get("total_capture_pct")
        ),
        "sharpe_ratio": (
            (artifact.summary or {}).get("sharpe_ratio")
        ),
        "persist_skip_bars": int(artifact.persist_skip_bars or 0),
        "engine": artifact.engine,
        "run_id": artifact.run_id,
    }


# ---------------------------------------------------------------------------
# Convenience builder for the Phase 6A preview
# ---------------------------------------------------------------------------


def build_impactsearch_day_artifact_from_local(
    target_ticker: str,
    signal_source: str,
    *,
    sig_lib_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    summary_overrides: Optional[Mapping[str, Any]] = None,
    persist_skip_bars: Optional[int] = None,
    run_id: Optional[str] = None,
) -> Optional[ResearchDayArtifact]:
    """Read the saved stable signal-library PKL for ``signal_source``
    and the Spymaster cache PKL for ``target_ticker``, then build the
    canonical research_day_v1 artifact. Returns None if either source
    is missing or unreadable.

    This is the single entrypoint the Phase 6A preview should call
    when materializing a chart artifact for the currently-selected
    row. It is bounded (one pair, one disk read each) and offline.
    """
    import pickle

    if not target_ticker or not signal_source:
        return None
    safe_target = _normalize_ticker_for_filename(target_ticker)
    safe_source = _normalize_ticker_for_filename(signal_source)
    if not safe_target or not safe_source:
        return None

    sig_base = (
        Path(sig_lib_dir) if sig_lib_dir is not None
        else _project_dir() / "signal_library" / "data" / "stable"
    )
    cache_base = (
        Path(cache_dir) if cache_dir is not None
        else _project_dir() / "cache" / "results"
    )
    sig_path = sig_base / f"{safe_source}_stable_v1_0_0.pkl"
    cache_path = cache_base / f"{safe_target}_precomputed_results.pkl"
    if not sig_path.exists() or not cache_path.exists():
        return None
    try:
        with sig_path.open("rb") as fh:
            sig_obj = pickle.load(fh)
        with cache_path.open("rb") as fh:
            cache_obj = pickle.load(fh)
    except Exception:
        return None
    if not isinstance(sig_obj, dict) or not isinstance(cache_obj, dict):
        return None
    sigs = sig_obj.get("primary_signals")
    sig_dates = sig_obj.get("dates")
    if sigs is None or sig_dates is None:
        return None
    pre = cache_obj.get("preprocessed_data")
    if pre is None or not isinstance(pre, pd.DataFrame):
        return None
    if "Close" not in pre.columns:
        return None
    closes = pd.to_numeric(pre["Close"], errors="coerce").dropna()
    if closes.empty:
        return None
    closes_df = pd.DataFrame({
        "date": pd.to_datetime(closes.index, errors="coerce"),
        "close": closes.values,
    }).dropna(subset=["date"]).sort_values("date").drop_duplicates(
        subset=["date"], keep="last",
    )
    sig_df = pd.DataFrame({
        "date": pd.to_datetime(list(sig_dates), errors="coerce"),
        "signal": [str(s).strip() for s in sigs],
    }).dropna(subset=["date"]).sort_values("date").drop_duplicates(
        subset=["date"], keep="last",
    )
    aligned = closes_df.merge(sig_df, on="date", how="inner")
    if aligned.empty:
        return None
    return build_impactsearch_day_artifact(
        target_ticker,
        signal_source,
        dates=aligned["date"].tolist(),
        signals=aligned["signal"].tolist(),
        target_close=aligned["close"].tolist(),
        persist_skip_bars=persist_skip_bars,
        run_id=run_id,
        summary_overrides=summary_overrides,
    )


# ---------------------------------------------------------------------------
# Phase 6B-2: StackBuilder day-by-day artifacts
# ---------------------------------------------------------------------------


# Member-string parser. Mirrors trafficflow.parse_members_with_protocol
# semantics ("AAA[D], BBB[I], CCC" -> [("AAA","D"), ("BBB","I"),
# ("CCC", None)]) so this module never imports trafficflow at runtime
# and stays free of Dash / Spymaster / TrafficFlow / Confluence import
# chains.
_MEMBERS_TOKEN_RX = re.compile(
    r"\s*([A-Za-z0-9_\-\.\^]+)(?:\s*\[\s*([DId])(?:[A-Za-z]*)\s*\])?\s*",
)


def parse_stack_members_with_protocol(
    members_str: Any,
) -> list[tuple[str, Optional[str]]]:
    """Parse a saved leaderboard ``Members`` string into a list of
    ``(ticker_upper, protocol)`` tuples. ``protocol`` is ``"D"``,
    ``"I"``, or ``None`` when absent. Brackets / extra annotations
    after the protocol letter are tolerated. Empty / non-string
    input returns ``[]``."""
    if members_str is None:
        return []
    s = str(members_str).strip()
    if not s:
        return []
    # Strip outer list brackets if present.
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    out: list[tuple[str, Optional[str]]] = []
    for part in s.split(","):
        part = part.strip().strip("'").strip('"')
        if not part:
            continue
        m = _MEMBERS_TOKEN_RX.fullmatch(part)
        if m is None:
            continue
        ticker = m.group(1).strip().upper()
        proto_raw = m.group(2)
        proto: Optional[str]
        if proto_raw is None:
            proto = None
        else:
            up = proto_raw.upper()
            proto = "D" if up == "D" else ("I" if up == "I" else None)
        if not ticker:
            continue
        out.append((ticker, proto))
    return out


def combine_member_signals(
    member_signals: Mapping[str, str],
    K: Optional[int] = None,
) -> str:
    """Apply the PRJCT9 / Spymaster combine rule across members:

      * None / Cash / missing -> neutral, ignored in the agreement
        check
      * all active members agree on Buy -> ``Buy``
      * all active members agree on Short -> ``Short``
      * mixed Buy and Short -> ``None``
      * no active members -> ``None``

    ``K`` (when provided and > 0) is the minimum number of agreeing
    active members required before the stack acts. If fewer than K
    members agree, the combined signal is ``None``.
    """
    if not member_signals:
        return "None"
    buy_n = 0
    short_n = 0
    for v in member_signals.values():
        s = str(v or "").strip().lower()
        if s == "buy":
            buy_n += 1
        elif s == "short":
            short_n += 1
    if buy_n > 0 and short_n > 0:
        return "None"
    if buy_n == 0 and short_n == 0:
        return "None"
    threshold = int(K) if (K is not None and int(K) > 0) else 1
    if buy_n >= threshold and short_n == 0:
        return "Buy"
    if short_n >= threshold and buy_n == 0:
        return "Short"
    return "None"


def _apply_protocol(
    raw_signal: str, protocol: Optional[str],
) -> str:
    """Apply Direct/Inverse protocol to a raw member signal.

    Direct (or unknown): pass through.
    Inverse: Buy -> Short, Short -> Buy, None -> None.
    """
    s = str(raw_signal or "").strip()
    if (protocol or "").upper() == "I":
        if s.lower() == "buy":
            return "Short"
        if s.lower() == "short":
            return "Buy"
        return "None"
    return s if s in ("Buy", "Short", "None") else "None"


def artifact_path_for_stackbuilder(
    target_ticker: str,
    run_id: str,
    K: int,
    base_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Return the canonical local artifact path for a StackBuilder
    (target, run, K) tuple. Returns None if either ticker normalizes
    to empty or run_id / K is missing."""
    safe_target = _normalize_ticker_for_filename(target_ticker)
    if not safe_target or not run_id:
        return None
    try:
        K_int = int(K)
    except Exception:
        return None
    safe_run = _normalize_ticker_for_filename(run_id) or str(run_id)
    base = (
        Path(base_dir) if base_dir is not None
        else _project_dir() / _DEFAULT_BASE
    )
    folder = base / "stackbuilder" / safe_target
    return (
        folder / f"{safe_run}__K{K_int}.research_day.json"
    )


def build_stackbuilder_day_artifact(
    target_ticker: str,
    run_id: str,
    K: int,
    *,
    dates: Sequence[Any],
    target_close: Sequence[float],
    member_signal_columns: Mapping[str, Sequence[Any]],
    protocol_per_member: Optional[Mapping[str, Optional[str]]] = None,
    persist_skip_bars: Optional[int] = None,
    metric_basis: str = "Close",
    summary_overrides: Optional[Mapping[str, Any]] = None,
) -> ResearchDayArtifact:
    """Build a StackBuilder day-by-day artifact from already-aligned
    member signal columns.

    ``member_signal_columns`` maps member ticker -> sequence of
    pre-protocol Buy / Short / None / "missing" strings, one per row
    in ``dates``. Direct/Inverse protocol is applied here so the
    saved daily rows reflect the post-protocol member signal each
    day. Combine rule + K gate produce ``combined_signal`` per day.

    Capture mapping mirrors ImpactSearch:
      * Buy combined day -> +pct_change(target_close) * 100
      * Short combined day -> -pct_change(target_close) * 100
      * None combined day -> 0
    T-1 persist skip applied to the trailing N bars before the cumsum.
    """
    if not isinstance(dates, (list, tuple, np.ndarray, pd.Series, pd.Index)):
        raise TypeError("dates must be a sequence")
    if not isinstance(target_close, (list, tuple, np.ndarray, pd.Series)):
        raise TypeError("target_close must be a sequence")
    if not isinstance(member_signal_columns, Mapping):
        raise TypeError("member_signal_columns must be a mapping")
    n = len(dates)
    if n != len(target_close):
        raise ValueError("dates and target_close must have equal length")
    for member, col in member_signal_columns.items():
        if len(col) != n:
            raise ValueError(
                f"member_signal_columns[{member!r}] length "
                f"{len(col)} != dates length {n}"
            )

    skip = (
        DEFAULT_PERSIST_SKIP_BARS if persist_skip_bars is None
        else int(persist_skip_bars)
    )
    proto = dict(protocol_per_member or {})
    members = list(member_signal_columns.keys())

    df = pd.DataFrame({
        "date": pd.to_datetime(list(dates), errors="coerce"),
        "target_close": pd.to_numeric(
            list(target_close), errors="coerce",
        ),
    })
    for m in members:
        df[m] = [str(v).strip() for v in member_signal_columns[m]]
    df = df[df["date"].notna()].sort_values("date").reset_index(drop=True)
    df["target_return_pct"] = (
        df["target_close"].pct_change().fillna(0.0) * 100.0
    )

    combined: list[str] = []
    member_signal_rows: list[dict] = []
    for _, row in df.iterrows():
        per_member: dict[str, str] = {}
        for m in members:
            raw = str(row[m] or "").strip()
            if not raw or raw.lower() == "missing":
                per_member[m] = "missing"
                continue
            per_member[m] = _apply_protocol(raw, proto.get(m))
        # Members marked "missing" don't count toward agreement.
        active = {
            m: s for m, s in per_member.items()
            if s in ("Buy", "Short", "None")
        }
        combined.append(combine_member_signals(active, K=K))
        member_signal_rows.append(per_member)

    df["combined_signal"] = combined
    sig_norm = df["combined_signal"].str.lower()
    df["daily_capture_pct"] = 0.0
    df.loc[sig_norm.eq("buy"), "daily_capture_pct"] = (
        df.loc[sig_norm.eq("buy"), "target_return_pct"]
    )
    df.loc[sig_norm.eq("short"), "daily_capture_pct"] = (
        -df.loc[sig_norm.eq("short"), "target_return_pct"]
    )
    df["is_trigger_day"] = sig_norm.isin({"buy", "short"})

    if skip and skip > 0 and len(df) > skip:
        df_trim = df.iloc[:-skip].copy()
        member_rows_trim = member_signal_rows[:-skip]
    else:
        df_trim = df.copy()
        member_rows_trim = list(member_signal_rows)
    df_trim["cumulative_capture_pct"] = (
        df_trim["daily_capture_pct"].cumsum()
    )

    trigger_mask = df_trim["is_trigger_day"]
    trigger_caps = df_trim.loc[trigger_mask, "daily_capture_pct"]
    n_trigger = int(trigger_mask.sum())
    if n_trigger > 0:
        total_capture_pct = float(trigger_caps.sum())
        avg_daily_capture_pct = float(trigger_caps.mean())
        wins = int((trigger_caps > 0).sum())
        losses = int((trigger_caps < 0).sum())
    else:
        total_capture_pct = 0.0
        avg_daily_capture_pct = 0.0
        wins = 0
        losses = 0
    if n_trigger > 1:
        std_dev = float(trigger_caps.std(ddof=1))
        sharpe_ratio = (
            avg_daily_capture_pct / std_dev if std_dev > 0
            else 0.0
        )
    else:
        sharpe_ratio = 0.0

    overrides = dict(summary_overrides or {})
    summary: dict = {
        "total_capture_pct": _safe_float(
            overrides.get("total_capture_pct", total_capture_pct),
        ),
        "avg_daily_capture_pct": _safe_float(
            overrides.get(
                "avg_daily_capture_pct", avg_daily_capture_pct,
            ),
        ),
        "sharpe_ratio": _safe_float(
            overrides.get("sharpe_ratio", sharpe_ratio),
        ),
        "trigger_days": _safe_int(
            overrides.get("trigger_days", n_trigger),
        ),
        "wins": _safe_int(overrides.get("wins", wins)),
        "losses": _safe_int(overrides.get("losses", losses)),
        "p_value": _safe_float(overrides.get("p_value")),
        "significant_95": (
            None if overrides.get("significant_95") is None
            else bool(overrides.get("significant_95"))
        ),
        "rebuilt_total_capture_pct": total_capture_pct,
        "rebuilt_sharpe_ratio": sharpe_ratio,
        "rebuilt_trigger_days": n_trigger,
    }

    daily: list[dict] = []
    for (_, row), member_row in zip(
        df_trim.iterrows(), member_rows_trim,
    ):
        daily.append({
            "date": _to_iso_date(row["date"]),
            "target_close": _safe_float(row["target_close"]),
            "target_return_pct": _safe_float(row["target_return_pct"]),
            "member_signals": dict(member_row),
            "combined_signal": str(row["combined_signal"]),
            "daily_capture_pct": _safe_float(row["daily_capture_pct"]),
            "cumulative_capture_pct": _safe_float(
                row["cumulative_capture_pct"],
            ),
            "is_trigger_day": bool(row["is_trigger_day"]),
        })

    return ResearchDayArtifact(
        artifact_version=ARTIFACT_VERSION,
        engine="stackbuilder",
        target_ticker=str(target_ticker).strip().upper(),
        signal_source="",
        run_id=str(run_id),
        metric_basis=str(metric_basis or "Close"),
        persist_skip_bars=int(skip),
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        summary=summary,
        daily=daily,
        K=int(K),
        members=list(members),
        protocol_per_member={m: proto.get(m) for m in members},
    )


def _normalize_active_pair_to_signal(value: Any) -> str:
    """Map a Spymaster ``active_pairs`` entry to a Buy / Short / None
    string. The historical pkl shape carries strings like
    ``"Buy 3,2"``, ``"Short 1,2"``, or ``"None"``. Anything else (or
    NaN) maps to ``"None"``."""
    if value is None:
        return "None"
    s = str(value).strip()
    if not s:
        return "None"
    head = s.split()[0].lower() if s else ""
    if head == "buy":
        return "Buy"
    if head == "short":
        return "Short"
    return "None"


def _extract_member_signals_from_spymaster_cache(
    cache_obj: Any,
) -> Optional[pd.DataFrame]:
    """Extract a ``date -> Buy/Short/None`` series from a real
    Spymaster cache PKL.

    Supports two shapes:

    1. ``primary_signals`` + ``dates`` (synthetic / signal-library
       shape used by Phase 6B-1's tests). Length-matched directly.
    2. ``preprocessed_data`` (DataFrame indexed by date) +
       ``active_pairs`` (list of strings like ``"Buy 3,2"`` /
       ``"Short 1,2"`` / ``"None"``). Mirrors
       ``spymaster._align_spymaster_active_pairs_to_dates``:
       - if ``len(active_pairs) == len(index)`` -> align to
         the full index;
       - if ``len(active_pairs) == len(index) - 1`` -> align to
         ``index[1:]`` (the historical PKL shape where the first
         preprocessed_data row has no derived signal);
       - otherwise the cache is unusable -> return None.

    Returns a 2-column DataFrame with ``date`` (Timestamp) and
    ``signal`` (Buy / Short / None) sorted by date with duplicates
    dropped. Returns None when neither shape resolves cleanly.

    Strictly read-only; never imports spymaster.
    """
    if not isinstance(cache_obj, dict):
        return None
    # Shape 1: primary_signals + dates.
    sigs = cache_obj.get("primary_signals")
    dates = cache_obj.get("dates")
    if sigs is not None and dates is not None and len(sigs) == len(dates):
        try:
            df = pd.DataFrame({
                "date": pd.to_datetime(list(dates), errors="coerce"),
                "signal": [str(s).strip() for s in sigs],
            }).dropna(subset=["date"]).sort_values(
                "date",
            ).drop_duplicates(subset=["date"], keep="last")
            if not df.empty:
                df["signal"] = df["signal"].map(
                    _normalize_active_pair_to_signal,
                )
                return df.reset_index(drop=True)
        except Exception:
            pass
    # Shape 2: preprocessed_data + active_pairs.
    pre = cache_obj.get("preprocessed_data")
    ap = cache_obj.get("active_pairs")
    if pre is None or ap is None:
        return None
    if not isinstance(pre, pd.DataFrame) or pre.empty:
        return None
    try:
        idx = pd.to_datetime(pre.index, errors="coerce")
    except Exception:
        return None
    n_idx = len(idx)
    n_ap = len(ap)
    if n_ap == n_idx:
        aligned_idx = idx
    elif n_ap == n_idx - 1:
        aligned_idx = idx[1:]
    else:
        return None
    try:
        df = pd.DataFrame({
            "date": aligned_idx,
            "signal": [
                _normalize_active_pair_to_signal(v) for v in ap
            ],
        })
    except Exception:
        return None
    df = df.dropna(subset=["date"]).sort_values(
        "date",
    ).drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    if df.empty:
        return None
    return df


def build_stackbuilder_day_artifact_from_local(
    target_ticker: str,
    run_id: str,
    *,
    members_str: str,
    K: int,
    summary_overrides: Optional[Mapping[str, Any]] = None,
    persist_skip_bars: Optional[int] = None,
    cache_dir: Optional[Path] = None,
) -> Optional[ResearchDayArtifact]:
    """Read each member's local Spymaster cache PKL plus the target's
    cache PKL, align dates, then build the StackBuilder day artifact.

    Returns None when:
      - the target cache PKL is missing/unreadable
      - ``members_str`` parses to no usable members
      - none of the members has a readable cache PKL

    Members whose cache PKL is missing render in daily rows as
    ``"missing"`` and are excluded from the agreement / K-gate
    calculation. Strictly read-only and offline; never imports
    trafficflow / spymaster / dash.
    """
    import pickle

    if not target_ticker or not run_id or not members_str:
        return None
    safe_target = _normalize_ticker_for_filename(target_ticker)
    if not safe_target:
        return None
    cache_base = (
        Path(cache_dir) if cache_dir is not None
        else _project_dir() / "cache" / "results"
    )
    target_path = cache_base / f"{safe_target}_precomputed_results.pkl"
    if not target_path.exists():
        return None
    try:
        with target_path.open("rb") as fh:
            target_obj = pickle.load(fh)
    except Exception:
        return None
    if not isinstance(target_obj, dict):
        return None
    pre = target_obj.get("preprocessed_data")
    if pre is None or not isinstance(pre, pd.DataFrame):
        return None
    if "Close" not in pre.columns:
        return None
    closes = pd.to_numeric(pre["Close"], errors="coerce").dropna()
    if closes.empty:
        return None
    closes_df = pd.DataFrame({
        "date": pd.to_datetime(closes.index, errors="coerce"),
        "close": closes.values,
    }).dropna(subset=["date"]).sort_values("date").drop_duplicates(
        subset=["date"], keep="last",
    ).reset_index(drop=True)

    parsed = parse_stack_members_with_protocol(members_str)
    if not parsed:
        return None
    member_dfs: dict[str, pd.DataFrame] = {}
    protocol_per_member: dict[str, Optional[str]] = {}
    for ticker, proto in parsed:
        protocol_per_member[ticker] = proto
        safe_member = _normalize_ticker_for_filename(ticker)
        if not safe_member:
            continue
        m_path = (
            cache_base / f"{safe_member}_precomputed_results.pkl"
        )
        if not m_path.exists():
            member_dfs[ticker] = None  # marker for missing
            continue
        try:
            with m_path.open("rb") as fh:
                m_obj = pickle.load(fh)
        except Exception:
            member_dfs[ticker] = None
            continue
        if not isinstance(m_obj, dict):
            member_dfs[ticker] = None
            continue
        # Member daily Buy/Short/None signal. Real Spymaster caches
        # carry ``preprocessed_data`` + ``active_pairs``; synthetic
        # / signal-library caches use ``primary_signals`` + ``dates``.
        # The extractor handles both. Members with neither readable
        # shape are flagged as ``None`` (missing) rather than guessed.
        m_df = _extract_member_signals_from_spymaster_cache(m_obj)
        if m_df is None or m_df.empty:
            member_dfs[ticker] = None
            continue
        member_dfs[ticker] = m_df

    have_any = any(df is not None for df in member_dfs.values())
    if not have_any:
        return None

    aligned = closes_df.copy()
    member_signal_columns: dict[str, list[str]] = {}
    for ticker, m_df in member_dfs.items():
        if m_df is None or m_df.empty:
            member_signal_columns[ticker] = ["missing"] * len(aligned)
            continue
        merged = aligned.merge(
            m_df, on="date", how="left",
        )["signal"].fillna("missing").tolist()
        member_signal_columns[ticker] = merged

    if aligned.empty:
        return None

    return build_stackbuilder_day_artifact(
        target_ticker,
        run_id,
        K,
        dates=aligned["date"].tolist(),
        target_close=aligned["close"].tolist(),
        member_signal_columns=member_signal_columns,
        protocol_per_member=protocol_per_member,
        persist_skip_bars=persist_skip_bars,
        summary_overrides=summary_overrides,
    )


# ---------------------------------------------------------------------------
# Phase 6B-2: catalogue index
# ---------------------------------------------------------------------------


CATALOGUE_INDEX_FILENAME = "catalogue_index.json"


def discover_research_artifacts(
    base_dir: Optional[Path] = None,
) -> list[Path]:
    """Walk ``output/research_artifacts/`` and return every saved
    ``*.research_day.json`` path (engine subdirs included). Returns
    an empty list when the tree is missing."""
    base = (
        Path(base_dir) if base_dir is not None
        else _project_dir() / _DEFAULT_BASE
    )
    if not base.exists() or not base.is_dir():
        return []
    out: list[Path] = []
    for engine_dir in sorted(base.iterdir()):
        if not engine_dir.is_dir():
            continue
        for path in sorted(engine_dir.rglob("*.research_day.json")):
            if path.is_file():
                out.append(path)
    return out


def build_research_catalogue_index(
    base_dir: Optional[Path] = None,
) -> dict:
    """Build the catalogue index dict from every saved artifact under
    ``base_dir``. Output schema:

        {
            "generated_at": iso-string,
            "counts": {
                "impactsearch": int,
                "stackbuilder": int,
                "confluence": int,
                "trafficflow": int,
            },
            "targets": [...sorted unique target tickers...],
            "entries": [
                {
                    "engine": str,
                    "target_ticker": str,
                    "signal_source": str | None,
                    "run_id": str | None,
                    "K": int | None,
                    "path": str,
                    "first_date": str | None,
                    "last_date": str | None,
                    "total_capture_pct": float | None,
                    "sharpe_ratio": float | None,
                    "trigger_days": int | None,
                },
                ...
            ],
        }
    """
    paths = discover_research_artifacts(base_dir)
    counts: dict[str, int] = {
        "impactsearch": 0,
        "stackbuilder": 0,
        "confluence": 0,
        "trafficflow": 0,
    }
    targets: set[str] = set()
    entries: list[dict] = []
    for p in paths:
        art = read_research_day_artifact(p)
        if art is None:
            continue
        engine = art.engine or "unknown"
        if engine in counts:
            counts[engine] += 1
        else:
            counts[engine] = counts.get(engine, 0) + 1
        if art.target_ticker:
            targets.add(art.target_ticker)
        s = summarize_research_day_artifact(art)
        entry = {
            "engine": engine,
            "target_ticker": art.target_ticker or None,
            "signal_source": art.signal_source or None,
            "run_id": art.run_id,
            "K": art.K,
            "path": str(p),
            "first_date": s.get("first_date"),
            "last_date": s.get("last_date"),
            "total_capture_pct": s.get("total_capture_pct"),
            "sharpe_ratio": s.get("sharpe_ratio"),
            "trigger_days": s.get("trigger_days"),
        }
        entries.append(entry)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        "counts": counts,
        "targets": sorted(targets),
        "entries": entries,
    }


def write_research_catalogue_index(
    base_dir: Optional[Path] = None,
) -> Path:
    """Persist the catalogue index JSON at
    ``<base_dir>/catalogue_index.json``. Returns the resolved path."""
    base = (
        Path(base_dir) if base_dir is not None
        else _project_dir() / _DEFAULT_BASE
    )
    base.mkdir(parents=True, exist_ok=True)
    payload = build_research_catalogue_index(base)
    out_path = base / CATALOGUE_INDEX_FILENAME
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    return out_path


def read_research_catalogue_index(
    base_dir: Optional[Path] = None,
) -> Optional[dict]:
    """Read the previously-written catalogue index JSON. Returns None
    when missing or unreadable."""
    base = (
        Path(base_dir) if base_dir is not None
        else _project_dir() / _DEFAULT_BASE
    )
    p = base / CATALOGUE_INDEX_FILENAME
    if not p.exists() or not p.is_file():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Phase 6B-3: Confluence day-by-day artifacts
# ---------------------------------------------------------------------------


# Default 7-tier-engine timeframes. Mirrors
# ``signal_library.confluence_analyzer.load_confluence_data`` defaults so
# this module never needs to import the analyzer at module load time.
CONFLUENCE_TIMEFRAMES_DEFAULT: list[str] = ["1d", "1wk", "1mo", "3mo", "1y"]
CONFLUENCE_MIN_ACTIVE_DEFAULT = 2


# 7-tier label set. Anything outside this set falls through to "None"
# / "Neutral" depending on call site.
_CONFLUENCE_TIER_TO_SIGNAL = {
    "strong buy": "Buy",
    "buy": "Buy",
    "weak buy": "Buy",
    "neutral": "None",
    "weak short": "Short",
    "short": "Short",
    "strong short": "Short",
}


def _normalize_confluence_tier(value: Any) -> str:
    """Coerce a raw tier label to one of the seven canonical strings.
    Unknown / missing -> ``"Neutral"``."""
    if value is None:
        return "Neutral"
    s = str(value).strip()
    if not s:
        return "Neutral"
    low = s.lower()
    if low == "strong buy":
        return "Strong Buy"
    if low == "buy":
        return "Buy"
    if low == "weak buy":
        return "Weak Buy"
    if low == "neutral":
        return "Neutral"
    if low == "weak short":
        return "Weak Short"
    if low == "short":
        return "Short"
    if low == "strong short":
        return "Strong Short"
    return "Neutral"


def confluence_tier_to_signal(tier: Any) -> str:
    """Phase 6B-3 mapping. Strong Buy / Buy / Weak Buy -> ``Buy``;
    Strong Short / Short / Weak Short -> ``Short``; Neutral / Unknown /
    missing -> ``None``."""
    if tier is None:
        return "None"
    return _CONFLUENCE_TIER_TO_SIGNAL.get(str(tier).strip().lower(), "None")


def _compute_confluence_tier_from_counts(
    buy: int,
    short: int,
    none: int,
    min_active: int = CONFLUENCE_MIN_ACTIVE_DEFAULT,
) -> str:
    """Pure O(1) confluence-tier helper.

    Mirrors ``signal_library.confluence_analyzer.calculate_confluence``
    decision rules without the analyzer's per-date
    ``alignment_since`` walk (which is O(N) and turns the analyzer's
    per-date call into O(N^2) when used inside an artifact builder).

    Inputs are the per-row Buy / Short / None timeframe counts. The
    ``active`` denominator follows the analyzer's
    ``max(1, total - none_count)`` rule so an all-None row collapses
    to Neutral via the ``min_active`` gate, not via a divide-by-zero.

    Tier thresholds (mirroring confluence_analyzer):
      * Strong Buy   : all active frames are Buy
      * Strong Short : all active frames are Short
      * Buy          : buy_pct >= 0.75 and short == 0
      * Short        : short_pct >= 0.75 and buy == 0
      * Weak Buy     : buy_pct >= 0.50 and short_pct < 0.25
      * Weak Short   : short_pct >= 0.50 and buy_pct < 0.25
      * Neutral      : everything else, including
                       active < min_active (the min-active gate).
    """
    b = int(buy)
    s = int(short)
    n = int(none)
    total = b + s + n
    active = max(1, total - n)  # = max(1, b + s)
    try:
        ma = int(min_active)
    except Exception:
        ma = CONFLUENCE_MIN_ACTIVE_DEFAULT
    if active < ma:
        return "Neutral"
    if b == active:
        return "Strong Buy"
    if s == active:
        return "Strong Short"
    buy_pct = b / active
    short_pct = s / active
    if buy_pct >= 0.75 and s == 0:
        return "Buy"
    if short_pct >= 0.75 and b == 0:
        return "Short"
    if buy_pct >= 0.50 and short_pct < 0.25:
        return "Weak Buy"
    if short_pct >= 0.50 and buy_pct < 0.25:
        return "Weak Short"
    return "Neutral"


def artifact_path_for_confluence(
    target_ticker: str,
    run_id: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Return the canonical local artifact path for a Confluence
    target. Confluence artifacts are one-per-target by default; the
    optional ``run_id`` lets multiple runs coexist (e.g., re-runs
    after a signal-library refresh).

    Returns None if the target normalizes to empty.
    """
    safe_target = _normalize_ticker_for_filename(target_ticker)
    if not safe_target:
        return None
    base = (
        Path(base_dir) if base_dir is not None
        else _project_dir() / _DEFAULT_BASE
    )
    folder = base / "confluence" / safe_target
    if run_id:
        suffix = _normalize_ticker_for_filename(run_id) or str(run_id)
        return folder / f"{safe_target}__{suffix}.research_day.json"
    return folder / f"{safe_target}.research_day.json"


_CONFLUENCE_TIER_KEYS = (
    "strong_buy", "buy", "weak_buy", "neutral",
    "weak_short", "short", "strong_short",
)

_TIER_TO_KEY = {
    "Strong Buy": "strong_buy",
    "Buy": "buy",
    "Weak Buy": "weak_buy",
    "Neutral": "neutral",
    "Weak Short": "weak_short",
    "Short": "short",
    "Strong Short": "strong_short",
}


def build_confluence_day_artifact(
    target_ticker: str,
    *,
    dates: Sequence[Any],
    target_close: Sequence[float],
    confluence_tiers: Sequence[Any],
    timeframe_signals: Sequence[Mapping[str, Any]],
    timeframes: Optional[Sequence[str]] = None,
    alignment_pcts: Optional[Sequence[Any]] = None,
    min_active: int = CONFLUENCE_MIN_ACTIVE_DEFAULT,
    persist_skip_bars: Optional[int] = None,
    metric_basis: str = "Close",
    run_id: Optional[str] = None,
    summary_overrides: Optional[Mapping[str, Any]] = None,
) -> ResearchDayArtifact:
    """Build the canonical day-by-day artifact for the Confluence
    engine.

    Each daily row carries:
      * ``confluence_tier``      : one of Strong Buy / Buy / Weak Buy /
                                   Neutral / Weak Short / Short /
                                   Strong Short.
      * ``confluence_signal``    : Buy / Short / None (from the tier
                                   via the documented mapping).
      * ``timeframe_signals``    : per-timeframe Buy / Short / None /
                                   ``"missing"`` snapshot.
      * ``alignment_pct``        : 0..100 alignment among active
                                   frames. Active denominator = Buy
                                   + Short only (not None and not
                                   missing). Mirrors the production
                                   confluence_analyzer's active-frame
                                   semantics.
      * ``buy_count`` / ``short_count``  : per-timeframe Buy / Short
                                   counts.
      * ``none_count``           : per-timeframe None count.
      * ``active_count``         : Buy + Short (excluding None and
                                   missing). This matches the
                                   production confluence engine; do
                                   not count ``None`` as active.
      * ``available_count``      : Buy + Short + None (loaded
                                   timeframes only, missing
                                   excluded). Useful when the caller
                                   needs a "non-missing" count
                                   without conflating None with
                                   active.

    Capture mapping mirrors ImpactSearch / Stack:
      * Buy day -> +pct_change(target_close) * 100
      * Short day -> -pct_change(target_close) * 100
      * None / Neutral / missing -> 0

    Trigger days = Buy or Short. T-1 persist skip applied to the
    trailing N bars before the cumulative sum.

    Summary block: ``total_capture_pct``, ``avg_daily_capture_pct``,
    ``sharpe_ratio``, ``trigger_days``, ``wins``, ``losses``,
    ``p_value``, ``significant_95``, plus rebuilt-from-rows mirrors
    ``rebuilt_total_capture_pct`` / ``rebuilt_sharpe_ratio`` /
    ``rebuilt_trigger_days``, plus ``tier_counts`` (one count per
    canonical tier).
    """
    if not isinstance(dates, (list, tuple, np.ndarray, pd.Series, pd.Index)):
        raise TypeError("dates must be a sequence")
    if not isinstance(target_close, (list, tuple, np.ndarray, pd.Series)):
        raise TypeError("target_close must be a sequence")
    if not isinstance(
        confluence_tiers, (list, tuple, np.ndarray, pd.Series),
    ):
        raise TypeError("confluence_tiers must be a sequence")
    if not isinstance(
        timeframe_signals, (list, tuple, np.ndarray, pd.Series),
    ):
        raise TypeError("timeframe_signals must be a sequence")
    n = len(dates)
    if (
        n != len(target_close) or n != len(confluence_tiers)
        or n != len(timeframe_signals)
    ):
        raise ValueError(
            "dates / target_close / confluence_tiers / "
            "timeframe_signals must have equal length"
        )
    if alignment_pcts is not None and len(alignment_pcts) != n:
        raise ValueError("alignment_pcts must have the same length as dates")

    skip = (
        DEFAULT_PERSIST_SKIP_BARS if persist_skip_bars is None
        else int(persist_skip_bars)
    )
    timeframes_list = list(timeframes) if timeframes else list(
        CONFLUENCE_TIMEFRAMES_DEFAULT,
    )
    timeframes_list = [str(tf) for tf in timeframes_list]

    df = pd.DataFrame({
        "date": pd.to_datetime(list(dates), errors="coerce"),
        "target_close": pd.to_numeric(list(target_close), errors="coerce"),
        "confluence_tier": [
            _normalize_confluence_tier(t) for t in confluence_tiers
        ],
    })
    df["confluence_signal"] = df["confluence_tier"].map(
        confluence_tier_to_signal,
    )

    tf_rows: list[dict[str, str]] = []
    for raw in timeframe_signals:
        snap = {}
        if isinstance(raw, Mapping):
            for tf in timeframes_list:
                v = raw.get(tf)
                if v is None:
                    snap[tf] = "missing"
                    continue
                vs = str(v).strip()
                if not vs:
                    snap[tf] = "missing"
                elif vs.lower() == "buy":
                    snap[tf] = "Buy"
                elif vs.lower() == "short":
                    snap[tf] = "Short"
                elif vs.lower() == "none":
                    snap[tf] = "None"
                else:
                    snap[tf] = "missing"
        else:
            snap = {tf: "missing" for tf in timeframes_list}
        tf_rows.append(snap)
    df["timeframe_signals"] = tf_rows

    buy_counts: list[int] = []
    short_counts: list[int] = []
    none_counts: list[int] = []
    active_counts: list[int] = []
    available_counts: list[int] = []
    for snap in tf_rows:
        b = sum(1 for v in snap.values() if v == "Buy")
        s = sum(1 for v in snap.values() if v == "Short")
        n_none = sum(1 for v in snap.values() if v == "None")
        # active_count = Buy + Short ONLY. Mirrors the production
        # confluence_analyzer.calculate_confluence's active-frame
        # rule (None is not active). available_count includes None
        # so a caller that needs the non-missing total has it.
        a = b + s
        avail = b + s + n_none
        buy_counts.append(b)
        short_counts.append(s)
        none_counts.append(n_none)
        active_counts.append(a)
        available_counts.append(avail)
    df["buy_count"] = buy_counts
    df["short_count"] = short_counts
    df["none_count"] = none_counts
    df["active_count"] = active_counts
    df["available_count"] = available_counts
    if alignment_pcts is None:
        # alignment_pct: max(buy, short) / active. min_active gates
        # Strong tiers; the alignment number is reported as 0 when
        # the active denominator is below ``min_active`` to mirror
        # the production analyzer's behavior.
        align_vals: list[Optional[float]] = []
        for b, s, a in zip(buy_counts, short_counts, active_counts):
            if a <= 0 or a < int(min_active):
                align_vals.append(0.0)
            else:
                align_vals.append(round(100.0 * max(b, s) / a, 1))
        df["alignment_pct"] = align_vals
    else:
        df["alignment_pct"] = [_safe_float(v) for v in alignment_pcts]

    df = df[df["date"].notna()].sort_values("date").reset_index(drop=True)
    df["target_return_pct"] = (
        df["target_close"].pct_change().fillna(0.0) * 100.0
    )
    sig_norm = df["confluence_signal"].str.lower()
    df["daily_capture_pct"] = 0.0
    df.loc[sig_norm.eq("buy"), "daily_capture_pct"] = (
        df.loc[sig_norm.eq("buy"), "target_return_pct"]
    )
    df.loc[sig_norm.eq("short"), "daily_capture_pct"] = (
        -df.loc[sig_norm.eq("short"), "target_return_pct"]
    )
    df["is_trigger_day"] = sig_norm.isin({"buy", "short"})

    if skip and skip > 0 and len(df) > skip:
        df_trim = df.iloc[:-skip].copy()
    else:
        df_trim = df.copy()
    df_trim["cumulative_capture_pct"] = (
        df_trim["daily_capture_pct"].cumsum()
    )

    trigger_mask = df_trim["is_trigger_day"]
    trigger_caps = df_trim.loc[trigger_mask, "daily_capture_pct"]
    n_trigger = int(trigger_mask.sum())
    if n_trigger > 0:
        total_capture_pct = float(trigger_caps.sum())
        avg_daily_capture_pct = float(trigger_caps.mean())
        wins = int((trigger_caps > 0).sum())
        losses = int((trigger_caps < 0).sum())
    else:
        total_capture_pct = 0.0
        avg_daily_capture_pct = 0.0
        wins = 0
        losses = 0
    if n_trigger > 1:
        std_dev = float(trigger_caps.std(ddof=1))
        sharpe_ratio = (
            avg_daily_capture_pct / std_dev if std_dev > 0
            else 0.0
        )
    else:
        sharpe_ratio = 0.0

    tier_counts = {k: 0 for k in _CONFLUENCE_TIER_KEYS}
    for tier in df_trim["confluence_tier"]:
        key = _TIER_TO_KEY.get(str(tier))
        if key is not None:
            tier_counts[key] += 1
        else:
            tier_counts["neutral"] += 1

    overrides = dict(summary_overrides or {})
    summary: dict = {
        "total_capture_pct": _safe_float(
            overrides.get("total_capture_pct", total_capture_pct),
        ),
        "avg_daily_capture_pct": _safe_float(
            overrides.get(
                "avg_daily_capture_pct", avg_daily_capture_pct,
            ),
        ),
        "sharpe_ratio": _safe_float(
            overrides.get("sharpe_ratio", sharpe_ratio),
        ),
        "trigger_days": _safe_int(
            overrides.get("trigger_days", n_trigger),
        ),
        "wins": _safe_int(overrides.get("wins", wins)),
        "losses": _safe_int(overrides.get("losses", losses)),
        "p_value": _safe_float(overrides.get("p_value")),
        "significant_95": (
            None if overrides.get("significant_95") is None
            else bool(overrides.get("significant_95"))
        ),
        "rebuilt_total_capture_pct": total_capture_pct,
        "rebuilt_sharpe_ratio": sharpe_ratio,
        "rebuilt_trigger_days": n_trigger,
        "tier_counts": tier_counts,
    }

    daily: list[dict] = []
    for _, row in df_trim.iterrows():
        daily.append({
            "date": _to_iso_date(row["date"]),
            "target_close": _safe_float(row["target_close"]),
            "target_return_pct": _safe_float(row["target_return_pct"]),
            "confluence_tier": str(row["confluence_tier"]),
            "confluence_signal": str(row["confluence_signal"]),
            "timeframe_signals": dict(row["timeframe_signals"]),
            "alignment_pct": _safe_float(row["alignment_pct"]),
            "buy_count": int(row["buy_count"]),
            "short_count": int(row["short_count"]),
            "none_count": int(row["none_count"]),
            "active_count": int(row["active_count"]),
            "available_count": int(row["available_count"]),
            "daily_capture_pct": _safe_float(row["daily_capture_pct"]),
            "cumulative_capture_pct": _safe_float(
                row["cumulative_capture_pct"],
            ),
            "is_trigger_day": bool(row["is_trigger_day"]),
        })

    return ResearchDayArtifact(
        artifact_version=ARTIFACT_VERSION,
        engine="confluence",
        target_ticker=str(target_ticker).strip().upper(),
        signal_source="",
        run_id=run_id,
        metric_basis=str(metric_basis or "Close"),
        persist_skip_bars=int(skip),
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        summary=summary,
        daily=daily,
        timeframes=timeframes_list,
        min_active=int(min_active),
    )


def _confluence_ticker_form_candidates(
    ticker: str,
) -> list[str]:
    """Return ordered, de-duplicated ticker-name forms to try when
    looking up local Confluence input files.

    Real local PKLs typically retain the original ticker (e.g.,
    ``^GSPC_precomputed_results.pkl``). The filename-safe form
    (``_GSPC``) is the artifact-output form. Some data flows save
    one, some save the other, so reads must try the real form first
    and the filename-safe form second.
    """
    real = str(ticker or "").strip().upper()
    safe = _normalize_ticker_for_filename(ticker)
    forms: list[str] = []
    for f in (real, safe):
        if f and f not in forms:
            forms.append(f)
    return forms


def _resolve_local_target_cache_path(
    ticker: str,
    cache_base: Path,
) -> tuple[Optional[Path], Optional[str]]:
    """Resolve the local Spymaster cache PKL path for ``ticker``,
    trying the real ticker form first and the filename-safe form
    second.

    Returns ``(path, form)`` when a candidate file exists, or
    ``(None, None)`` when neither form has a cache on disk. The
    ``form`` is the ticker-name form that produced the resolved
    path so callers can pass that exact form into helpers that
    re-derive a filename later.
    """
    for form in _confluence_ticker_form_candidates(ticker):
        p = cache_base / f"{form}_precomputed_results.pkl"
        if p.exists():
            return p, form
    return None, None


def _resolve_local_signal_library_form(
    ticker: str,
    sig_base: Path,
    timeframes_list: Sequence[str],
) -> Optional[str]:
    """Resolve the ticker-name form for which at least one local
    signal-library PKL exists in ``sig_base``. Real ticker form
    (e.g. ``^GSPC``) wins over the filename-safe form (``_GSPC``);
    returns None when neither form has any matching library file.
    """
    for form in _confluence_ticker_form_candidates(ticker):
        if (sig_base / f"{form}_stable_v1_0_0.pkl").exists():
            return form
        for tf in timeframes_list:
            if tf == "1d":
                continue
            if (sig_base / f"{form}_stable_v1_0_0_{tf}.pkl").exists():
                return form
    return None


def _resolve_local_ticker_form(
    ticker: str,
    sig_base: Path,
    cache_base: Path,
    timeframes_list: Sequence[str],
) -> Optional[str]:
    """Convenience helper: returns the ticker-name form that has
    actual files on disk, preferring the form that produced a target
    Spymaster cache hit. Falls back to the signal-library form when
    no cache file exists. Returns None when neither resolver finds
    anything.

    This is a thin wrapper around the two split resolvers
    (``_resolve_local_target_cache_path`` and
    ``_resolve_local_signal_library_form``). Prefer the split form
    in callers that need to handle mixed-form fixtures (cache and
    library saved under different ticker forms).
    """
    _path, cache_form = _resolve_local_target_cache_path(
        ticker, cache_base,
    )
    if cache_form is not None:
        return cache_form
    return _resolve_local_signal_library_form(
        ticker, sig_base, timeframes_list,
    )


def build_confluence_day_artifact_from_local(
    target_ticker: str,
    *,
    persist_skip_bars: Optional[int] = None,
    min_active: int = CONFLUENCE_MIN_ACTIVE_DEFAULT,
    sig_lib_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    timeframes: Optional[Sequence[str]] = None,
    summary_overrides: Optional[Mapping[str, Any]] = None,
    run_id: Optional[str] = None,
) -> Optional[ResearchDayArtifact]:
    """Build the Confluence day-by-day artifact for ``target_ticker``
    using saved local timeframe libraries + the local Spymaster cache.

    Calls the production
    ``signal_library.confluence_analyzer`` engine
    (``load_confluence_data`` + ``align_signals_to_daily`` +
    ``calculate_confluence``) on saved local libraries; reads
    ``cache/results/<TARGET>_precomputed_results.pkl`` for the daily
    Close series. Strictly read-only / offline. Never imports
    ``confluence.py`` (Dash-heavy) or ``trafficflow.py``.

    Returns None when:
      - target normalizes to empty
      - no saved libraries exist for the target
      - the target Spymaster cache is missing or has no Close column
      - alignment grid is empty
      - the confluence_analyzer cannot be imported

    The Phase 6A preview button serializes one artifact per click; no
    universe scan, no batch.
    """
    import pickle

    if not target_ticker:
        return None
    safe_target = _normalize_ticker_for_filename(target_ticker)
    if not safe_target:
        return None
    timeframes_list = list(timeframes) if timeframes else list(
        CONFLUENCE_TIMEFRAMES_DEFAULT,
    )

    project_dir = _project_dir()
    sig_base = (
        Path(sig_lib_dir) if sig_lib_dir is not None
        else project_dir / "signal_library" / "data" / "stable"
    )
    cache_base = (
        Path(cache_dir) if cache_dir is not None
        else project_dir / "cache" / "results"
    )

    # Resolve cache and library form INDEPENDENTLY. Real local PKLs
    # typically retain the original symbol (e.g.,
    # ``^GSPC_precomputed_results.pkl``); the filename-safe form
    # (``_GSPC``) is the artifact-output form. The two file kinds
    # may live under different ticker forms (mixed-form fixtures),
    # so we resolve each side separately.
    target_pkl, cache_form = _resolve_local_target_cache_path(
        target_ticker, cache_base,
    )
    if target_pkl is None:
        return None
    library_form = _resolve_local_signal_library_form(
        target_ticker, sig_base, timeframes_list,
    )
    # If neither form has any library file, the analyzer's 1d
    # Spymaster-cache fallback can still satisfy "any signal" using
    # the resolved target cache. The analyzer is invoked with
    # ``library_form`` if available, otherwise with ``cache_form``
    # so the fallback receives a ticker name that points at a real
    # cache file.
    analyzer_form = library_form or cache_form
    if analyzer_form is None:
        return None

    try:
        with target_pkl.open("rb") as fh:
            target_obj = pickle.load(fh)
    except Exception:
        return None
    if not isinstance(target_obj, dict):
        return None
    pre = target_obj.get("preprocessed_data")
    if pre is None or not isinstance(pre, pd.DataFrame):
        return None
    if "Close" not in pre.columns:
        return None
    closes = pd.to_numeric(pre["Close"], errors="coerce").dropna()
    if closes.empty:
        return None
    closes_df = pd.DataFrame({
        "date": pd.to_datetime(closes.index, errors="coerce"),
        "close": closes.values,
    }).dropna(subset=["date"]).sort_values("date").drop_duplicates(
        subset=["date"], keep="last",
    ).reset_index(drop=True)

    # The analyzer reads relative paths and a hard-coded
    # ``cache/results`` prefix for its 1d Spymaster fallback. To
    # confine all reads to the user-supplied ``sig_lib_dir`` /
    # ``cache_dir`` we monkey-patch the analyzer module attributes
    # for the duration of this call (and restore them afterwards).
    saved_cwd = os.getcwd()
    try:
        try:
            os.chdir(project_dir)
        except Exception:
            pass
        try:
            import signal_library.confluence_analyzer as _ca
            from signal_library.confluence_analyzer import (
                align_signals_to_daily,
                load_signal_library_interval,
            )
        except Exception:
            return None
        saved_sig_dir = getattr(_ca, "SIGNAL_LIBRARY_DIR", None)
        saved_fallback = getattr(_ca, "_load_spymaster_cache_fallback", None)

        def _local_spymaster_fallback(
            ticker: str, _cache_base=cache_base,
        ):
            """In-place override that resolves the Spymaster cache via
            the user-supplied ``cache_dir`` instead of the analyzer's
            hard-coded ``cache/results`` relative path. Tries the
            real ticker form first and the filename-safe form second
            so caret-style symbols (e.g., ``^GSPC``) resolve."""
            spymaster_path = None
            for form in _confluence_ticker_form_candidates(ticker):
                p = (
                    Path(_cache_base)
                    / f"{form}_precomputed_results.pkl"
                )
                if p.exists():
                    spymaster_path = p
                    break
            if spymaster_path is None:
                return None
            try:
                with spymaster_path.open("rb") as fh:
                    sd = pickle.load(fh)
            except Exception:
                return None
            if not isinstance(sd, dict):
                return None
            buy_map = sd.get("daily_top_buy_pairs") or {}
            short_map = sd.get("daily_top_short_pairs") or {}
            if not isinstance(buy_map, dict) or not buy_map:
                return None
            dates = list(buy_map.keys())
            signals: list = []
            try:
                MAX_SMA_DAY = int(
                    getattr(_ca, "MAX_SMA_DAY", 114),
                )
            except Exception:
                MAX_SMA_DAY = 114
            for d in dates:
                _bp, bcap = buy_map.get(
                    d, ((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0),
                )
                _sp, scap = short_map.get(
                    d, ((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0),
                )
                if bcap > scap:
                    signals.append("Buy")
                elif scap > bcap:
                    signals.append("Short")
                else:
                    signals.append("None")
            return {
                "dates": dates,
                "signals": signals,
                "primary_signals": signals,
                "source": "spymaster_cache",
            }

        try:
            _ca.SIGNAL_LIBRARY_DIR = str(sig_base)
            _ca._load_spymaster_cache_fallback = _local_spymaster_fallback
            try:
                libs = {}
                for tf in timeframes_list:
                    try:
                        lib = load_signal_library_interval(
                            analyzer_form, tf,
                        )
                    except Exception:
                        lib = None
                    if lib is None:
                        continue
                    if "signals" not in lib and "primary_signals" in lib:
                        lib["signals"] = lib["primary_signals"]
                    if "dates" not in lib and "date_index" in lib:
                        lib["dates"] = lib["date_index"]
                    if "signals" not in lib or "dates" not in lib:
                        continue
                    libs[tf] = lib
            except Exception:
                libs = {}
            if not libs:
                return None
            try:
                aligned = align_signals_to_daily(libs)
            except Exception:
                return None
            if aligned is None or aligned.empty:
                return None

            # Linear-time per-row tier computation. The production
            # analyzer's ``calculate_confluence`` walks backward to
            # compute ``alignment_since`` (used by the snapshot UI
            # but NOT by the artifact day-by-day path). That walk
            # is O(N) per call, so calling it per date over a long
            # history (e.g. ^GSPC's 25500-day grid) becomes O(N^2)
            # and exceeds 6 minutes. The pure helper
            # ``_compute_confluence_tier_from_counts`` mirrors the
            # analyzer's tier rules in O(1), keeping the artifact
            # build time linear in the number of dates.
            tier_per_date: list[str] = []
            snap_per_date: list[dict] = []
            available_columns = [
                tf for tf in timeframes_list if tf in aligned.columns
            ]
            row_records = aligned[available_columns].to_dict(
                orient="records",
            )
            aligned_index_list = list(aligned.index)
            for raw_row in row_records:
                snap: dict[str, str] = {}
                for tf in timeframes_list:
                    if tf not in available_columns:
                        snap[tf] = "missing"
                        continue
                    v = raw_row.get(tf)
                    if v is None:
                        snap[tf] = "missing"
                        continue
                    try:
                        if pd.isna(v):
                            snap[tf] = "missing"
                            continue
                    except Exception:
                        pass
                    vs = str(v).strip()
                    if not vs:
                        snap[tf] = "missing"
                    elif vs.lower() == "buy":
                        snap[tf] = "Buy"
                    elif vs.lower() == "short":
                        snap[tf] = "Short"
                    elif vs.lower() == "none":
                        snap[tf] = "None"
                    else:
                        snap[tf] = "missing"
                buy_n = sum(1 for x in snap.values() if x == "Buy")
                short_n = sum(1 for x in snap.values() if x == "Short")
                none_n = sum(1 for x in snap.values() if x == "None")
                tier = _compute_confluence_tier_from_counts(
                    buy_n, short_n, none_n, min_active=min_active,
                )
                tier_per_date.append(tier)
                snap_per_date.append(snap)

            align_df = pd.DataFrame({
                "date": aligned_index_list,
                "tier": tier_per_date,
                "snap": snap_per_date,
            })
            align_df["date"] = pd.to_datetime(
                align_df["date"], errors="coerce",
            )
            align_df = align_df.dropna(subset=["date"]).reset_index(drop=True)
        finally:
            try:
                if saved_sig_dir is not None:
                    _ca.SIGNAL_LIBRARY_DIR = saved_sig_dir
                if saved_fallback is not None:
                    _ca._load_spymaster_cache_fallback = saved_fallback
            except Exception:
                pass
    finally:
        try:
            os.chdir(saved_cwd)
        except Exception:
            pass

    merged = closes_df.merge(align_df, on="date", how="inner")
    if merged.empty:
        return None
    # Do not pass ``alignment_pcts``: the builder recomputes it from
    # the per-row buy / short / active counts using the production
    # analyzer's max(buy_pct, short_pct) rule and the min_active
    # gate. Identical to the analyzer's value when active >=
    # min_active, and 0.0 when below the gate.
    return build_confluence_day_artifact(
        target_ticker,
        dates=merged["date"].tolist(),
        target_close=merged["close"].tolist(),
        confluence_tiers=merged["tier"].tolist(),
        timeframe_signals=merged["snap"].tolist(),
        timeframes=timeframes_list,
        min_active=min_active,
        persist_skip_bars=persist_skip_bars,
        run_id=run_id,
        summary_overrides=summary_overrides,
    )


# Phase 6B-4 / 6C scope reminders kept in source so a future
# maintainer sees the intended extension points.
#
#   TODO Phase 6B-4: build_trafficflow_day_artifact(...). Daily rows
#   gain per-member Buy/Short/None pressure + aggregate pressure
#   counts. Path:
#     output/research_artifacts/trafficflow/<TARGET>/<RUN_ID>.research_day.json
#
#   TODO Phase 6C: public catalogue UX + server caching model. The
#   catalogue index above is the seed.
