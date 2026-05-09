"""Phase 6B-1: canonical day-by-day research artifacts.

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

Phase 6B-1 wires only the ImpactSearch single-signal slice. Future
phases extend the same shape:

  TODO Phase 6B-2: StackBuilder day-by-day stack capture artifacts
                   (engine="stackbuilder").
  TODO Phase 6B-3: Confluence day-by-day confluence artifacts
                   (engine="confluence").
  TODO Phase 6B-4: Traffic Flow per-day pressure artifacts
                   (engine="trafficflow").

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


# Default T-1 persistence skip. Mirrors ``impactsearch.PERSIST_SKIP_BARS``
# (= 1) but is resolved lazily so importing this module never pulls
# the heavy ImpactSearch import graph.
def _resolve_default_skip() -> int:
    """Return ``impactsearch.PERSIST_SKIP_BARS`` if import-safe,
    otherwise the documented default of 1. Lazy / never raises."""
    try:
        import impactsearch  # noqa: F401
        v = getattr(impactsearch, "PERSIST_SKIP_BARS", None)
        if isinstance(v, int) and v >= 0:
            return v
    except Exception:
        pass
    return 1


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
    rehydrates the same shape."""

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
        _resolve_default_skip() if persist_skip_bars is None
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
