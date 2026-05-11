"""Phase 6D-2: multi-timeframe TrafficFlow / K-build bridge builder.

Closes the second half of the Phase 6C-8 audit's "TrafficFlow
bridge" gap: reads the saved daily TrafficFlow ``research_day_v1``
artifacts produced by Phase 6D-1 and projects each K build onto
the canonical multi-timeframe set
``("1d", "1wk", "1mo", "3mo", "1y")``. The resulting
``__MTF`` artifact carries a per-day combined pressure signal,
a per-timeframe map, the full capture path, and the original K
/ members / protocol metadata.

This module does **not** build Confluence artifacts. It only
materializes the TrafficFlow side of the bridge so the readiness
layer can stop emitting
``missing_multitimeframe_trafficflow_bridge`` once K=1..12 MTF
artifacts exist for a ticker.

Strictly read-only / offline:

  - No yfinance import.
  - No trafficflow.py / spymaster.py / impactsearch.py / dash
    import (statically asserted in the test suite).
  - Builder writes ``research_day_v1`` artifacts ONLY when invoked
    with ``write=True``; the web tier never touches this module.

Path / collision convention
---------------------------

The daily Phase 6D-1 builder writes ``<SEED_RUN>__K<K>.research_day.json``
under ``output/research_artifacts/trafficflow/<SAFE_TARGET>/``.
This module appends ``__MTF`` to the run id so the multi-
timeframe artifact never overwrites the underlying daily input:

    <SEED_RUN>__K<K>__MTF.research_day.json

The artifact's internal ``K`` value matches the daily input;
``timeframes`` is set to the full requested list so audit tooling
can detect the bridge presence by looking at ``timeframes`` and
``K`` together.

Projection semantics
--------------------

For each requested timeframe:

  * ``1d`` -> the daily ``pressure_signal`` is used as-is.
  * ``1wk`` / ``1mo`` / ``3mo`` / ``1y`` -> ``resample(<freq>).last()``
    is taken on the daily signal series, then reindexed onto the
    daily grid via ``method="ffill"``. A daily row therefore
    sees the **previous closed** period's signal; the current
    period contributes only on its closing day. There is no
    future-period leak.

Pandas frequency aliases (pandas 2.2):

  * 1wk -> ``W``     (week-end Sunday)
  * 1mo -> ``ME``    (month-end)
  * 3mo -> ``QE``    (calendar quarter-end)
  * 1y  -> ``YE``    (year-end)

Combine rule per daily row:

  * Active signals = timeframes whose value is ``Buy`` or
    ``Short``. ``None`` and ``missing`` do NOT contribute.
  * All active Buy   -> ``Buy``
  * All active Short -> ``Short``
  * Mixed or no active -> ``None``

Capture math: same as daily TrafficFlow / Confluence:

  * Buy day   -> ``+pct_change(target_close) * 100``
  * Short day -> ``-pct_change(target_close) * 100``
  * None day  -> ``0``
  * Trigger days = Buy + Short
  * T-1 persist skip = 1 bar (matches the rest of the engine
    family)

Public surface
--------------

    DEFAULT_TIMEFRAMES                          # tuple[str, ...]
    DEFAULT_EXPECTED_K                          # tuple[int, ...]
    MTF_SUFFIX                                  # "__MTF"
    DEFAULT_PERSIST_SKIP_BARS                   # int = 1

    PRESSURE_SIGNAL_BUY / SHORT / NONE / MISSING

    ISSUE_*                                     # str constants
    BuildResult                                 # dataclass

    artifact_run_id_for_multitimeframe(daily_run_id) -> str
    project_signal_to_timeframes(daily_dates, daily_signals,
                                 timeframes) -> dict[str, list[str]]
    combine_timeframe_signals(per_tf_signals) -> str
    build_multitimeframe_bridge_for_artifact(artifact, *,
        timeframes=DEFAULT_TIMEFRAMES,
        persist_skip_bars=DEFAULT_PERSIST_SKIP_BARS) -> ResearchDayArtifact
    build_multitimeframe_bridge_artifacts_for_target(target_ticker, *,
        artifact_root=None, expected_k=DEFAULT_EXPECTED_K,
        timeframes=DEFAULT_TIMEFRAMES, write=False) -> BuildResult
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import research_artifacts as _ra


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEFRAMES: tuple[str, ...] = ("1d", "1wk", "1mo", "3mo", "1y")
DEFAULT_EXPECTED_K: tuple[int, ...] = tuple(range(1, 13))
MTF_SUFFIX = "__MTF"
DEFAULT_PERSIST_SKIP_BARS = 1

PRESSURE_SIGNAL_BUY = "Buy"
PRESSURE_SIGNAL_SHORT = "Short"
PRESSURE_SIGNAL_NONE = "None"
PRESSURE_SIGNAL_MISSING = "missing"

# Pandas-2.2 resample alias for each timeframe (1d resamples to
# itself; downstream code skips the resample for 1d).
_TIMEFRAME_TO_FREQ: dict[str, Optional[str]] = {
    "1d": None,
    "1wk": "W",
    "1mo": "ME",
    "3mo": "QE",
    "1y": "YE",
}

# Issue codes surfaced on the BuildResult. Stable strings so audit
# tooling can branch without translation.
ISSUE_NO_DAILY_K_ARTIFACTS = "no_daily_k_artifacts"
ISSUE_INPUT_ARTIFACT_UNREADABLE = "input_artifact_unreadable"
ISSUE_INPUT_ARTIFACT_NO_DAILY = "input_artifact_no_daily"
ISSUE_PARTIAL_K_COVERAGE = "partial_k_coverage"
ISSUE_ARTIFACT_WRITE_FAILED = "artifact_write_failed"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BuildResult:
    """Outcome of a single
    ``build_multitimeframe_bridge_artifacts_for_target`` call.

    ``attempted_k`` records the K values seen on the saved
    daily TrafficFlow artifacts for this ticker intersected with
    ``expected_k``. ``built_k`` records the K values for which an
    MTF artifact was produced (and persisted when
    ``write=True``). ``skipped_k`` records K values that failed
    projection. ``issue_codes`` deduplicates findings in
    first-seen order.
    """

    target_ticker: str
    attempted_k: tuple[int, ...] = ()
    built_k: tuple[int, ...] = ()
    skipped_k: tuple[int, ...] = ()
    artifact_paths: tuple[Path, ...] = ()
    issue_codes: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_artifact_root() -> Path:
    return _project_dir() / "output" / "research_artifacts"


def _filename_safe_ticker(ticker: str) -> str:
    if not ticker:
        return ""
    s = str(ticker).strip().upper()
    if not s:
        return ""
    s = s.replace("^", "_")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")
    return "".join(c if c in allowed else "_" for c in s)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _safe_int(value: Any) -> Optional[int]:
    f = _safe_float(value)
    if f is None:
        return None
    return int(round(f))


def _normalize_signal(value: Any) -> str:
    """Coerce an arbitrary signal cell into one of the canonical
    pressure-signal strings (``Buy`` / ``Short`` / ``None`` /
    ``missing``). Anything we don't recognize collapses to
    ``missing`` so it stays out of the active count."""
    if value is None:
        return PRESSURE_SIGNAL_MISSING
    s = str(value).strip()
    if not s:
        return PRESSURE_SIGNAL_MISSING
    low = s.lower()
    if low == "buy":
        return PRESSURE_SIGNAL_BUY
    if low == "short":
        return PRESSURE_SIGNAL_SHORT
    if low == "none":
        return PRESSURE_SIGNAL_NONE
    return PRESSURE_SIGNAL_MISSING


def artifact_run_id_for_multitimeframe(daily_run_id: str) -> str:
    """Append ``__MTF`` to a Phase 6D-1 daily run id so the
    multi-timeframe artifact never overwrites its daily input.
    Empty input returns ``""`` so callers can surface a clean
    error rather than producing nameless artifacts."""
    if not daily_run_id:
        return ""
    return f"{str(daily_run_id).strip()}{MTF_SUFFIX}"


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def project_signal_to_timeframes(
    daily_dates: Sequence[Any],
    daily_signals: Sequence[Any],
    timeframes: Iterable[str] = DEFAULT_TIMEFRAMES,
) -> dict[str, list[str]]:
    """Project a daily pressure-signal series onto the requested
    calendar timeframes, returning a dict of
    ``timeframe -> list[str]`` aligned to the input daily grid.

    Semantics: for each non-daily timeframe, the period's last
    available signal is recorded on the period close date, then
    reindexed onto the daily grid with ``ffill``. A daily row
    therefore uses the most-recent CLOSED period's signal. Daily
    rows before any period has closed report ``"missing"``.

    The ``1d`` timeframe (when requested) returns the normalized
    daily signal series unchanged.
    """
    import pandas as pd

    if not daily_dates or not daily_signals:
        return {tf: [] for tf in timeframes}
    if len(daily_dates) != len(daily_signals):
        raise ValueError(
            "daily_dates and daily_signals must have the same length"
        )

    parsed_dates = pd.to_datetime(list(daily_dates), errors="coerce")
    normalized = [_normalize_signal(v) for v in daily_signals]
    base = pd.Series(normalized, index=pd.DatetimeIndex(parsed_dates))

    out: dict[str, list[str]] = {}
    for tf in timeframes:
        freq = _TIMEFRAME_TO_FREQ.get(tf)
        if freq is None:
            # 1d (or unknown -> treat as identity, kept defensive).
            out[tf] = list(normalized)
            continue
        period_last = base.resample(freq).last()
        projected = period_last.reindex(base.index, method="ffill")
        out[tf] = [
            _normalize_signal(v) if v is not None else PRESSURE_SIGNAL_MISSING
            for v in projected.tolist()
        ]
    return out


# ---------------------------------------------------------------------------
# Combine rule
# ---------------------------------------------------------------------------


def combine_timeframe_signals(per_tf_signals: Mapping[str, str]) -> str:
    """Strict-unanimity combine across active timeframes.

    Active = Buy or Short. ``None`` and ``missing`` do not
    contribute. All active Buy -> Buy. All active Short -> Short.
    Mixed (some Buy + some Short) or empty active set -> None.
    """
    active = [
        v for v in per_tf_signals.values()
        if v in (PRESSURE_SIGNAL_BUY, PRESSURE_SIGNAL_SHORT)
    ]
    if not active:
        return PRESSURE_SIGNAL_NONE
    if all(v == PRESSURE_SIGNAL_BUY for v in active):
        return PRESSURE_SIGNAL_BUY
    if all(v == PRESSURE_SIGNAL_SHORT for v in active):
        return PRESSURE_SIGNAL_SHORT
    return PRESSURE_SIGNAL_NONE


# ---------------------------------------------------------------------------
# Per-artifact build
# ---------------------------------------------------------------------------


def build_multitimeframe_bridge_for_artifact(
    artifact: _ra.ResearchDayArtifact,
    *,
    timeframes: Iterable[str] = DEFAULT_TIMEFRAMES,
    persist_skip_bars: Optional[int] = None,
) -> _ra.ResearchDayArtifact:
    """Build the MTF ``ResearchDayArtifact`` for one daily
    TrafficFlow input artifact. Returns a new artifact with
    ``engine="trafficflow"``, ``run_id`` set to the MTF-suffixed
    id, ``timeframes`` set to the requested list, and the daily
    rows fully populated per the Phase 6D-2 contract.

    Raises ``ValueError`` when the input artifact has no daily
    rows or no usable date/signal/close columns - both surface
    via ``ISSUE_INPUT_ARTIFACT_NO_DAILY`` upstream.
    """
    import pandas as pd

    if artifact is None or not getattr(artifact, "daily", None):
        raise ValueError("input artifact has no daily rows")

    daily_in = list(artifact.daily)
    dates = [row.get("date") for row in daily_in]
    closes = [_safe_float(row.get("target_close")) for row in daily_in]
    signals = [_normalize_signal(row.get("pressure_signal")) for row in daily_in]

    if not dates or all(d is None for d in dates):
        raise ValueError("input artifact daily rows lack dates")

    tf_list = list(timeframes)
    projected = project_signal_to_timeframes(dates, signals, tf_list)

    # Build aligned dataframe for the capture math.
    parsed_dates = pd.to_datetime(dates, errors="coerce")
    df = pd.DataFrame({
        "date": parsed_dates,
        "target_close": closes,
    })
    df["target_close"] = pd.to_numeric(df["target_close"], errors="coerce")
    df = df[df["date"].notna()].reset_index(drop=True)
    if df.empty:
        raise ValueError("input artifact has no parseable dates")
    df["target_return_pct"] = (
        df["target_close"].pct_change().fillna(0.0) * 100.0
    )

    # Trim projected columns to the rows that survived the dropna
    # above. Build them as parallel lists keyed on the row index.
    surviving_mask = pd.to_datetime(dates, errors="coerce").notna()
    tf_aligned: dict[str, list[str]] = {}
    for tf in tf_list:
        col = projected[tf]
        tf_aligned[tf] = [
            v for v, keep in zip(col, surviving_mask) if keep
        ]

    pressures: list[str] = []
    tf_maps: list[dict[str, str]] = []
    buy_counts: list[int] = []
    short_counts: list[int] = []
    none_counts: list[int] = []
    missing_counts: list[int] = []
    active_counts: list[int] = []
    available_counts: list[int] = []
    for i in range(len(df)):
        per_tf: dict[str, str] = {
            tf: tf_aligned[tf][i] for tf in tf_list
        }
        b = sum(1 for v in per_tf.values() if v == PRESSURE_SIGNAL_BUY)
        s = sum(1 for v in per_tf.values() if v == PRESSURE_SIGNAL_SHORT)
        nn = sum(1 for v in per_tf.values() if v == PRESSURE_SIGNAL_NONE)
        miss = sum(
            1 for v in per_tf.values() if v == PRESSURE_SIGNAL_MISSING
        )
        active = b + s
        available = b + s + nn  # excludes missing
        pressures.append(combine_timeframe_signals(per_tf))
        tf_maps.append(per_tf)
        buy_counts.append(b)
        short_counts.append(s)
        none_counts.append(nn)
        missing_counts.append(miss)
        active_counts.append(active)
        available_counts.append(available)

    df["pressure_signal"] = pressures
    df["timeframe_pressure_signals"] = tf_maps
    df["buy_count"] = buy_counts
    df["short_count"] = short_counts
    df["none_count"] = none_counts
    df["missing_count"] = missing_counts
    df["active_count"] = active_counts
    df["available_count"] = available_counts
    sig_norm = df["pressure_signal"].str.lower()
    df["daily_capture_pct"] = 0.0
    df.loc[sig_norm.eq("buy"), "daily_capture_pct"] = (
        df.loc[sig_norm.eq("buy"), "target_return_pct"]
    )
    df.loc[sig_norm.eq("short"), "daily_capture_pct"] = (
        -df.loc[sig_norm.eq("short"), "target_return_pct"]
    )
    df["is_trigger_day"] = sig_norm.isin({"buy", "short"})

    skip = (
        DEFAULT_PERSIST_SKIP_BARS if persist_skip_bars is None
        else int(persist_skip_bars)
    )
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
            avg_daily_capture_pct / std_dev if std_dev > 0 else 0.0
        )
    else:
        sharpe_ratio = 0.0

    pressure_counts = {
        "buy": int((df_trim["pressure_signal"] == PRESSURE_SIGNAL_BUY).sum()),
        "short": int(
            (df_trim["pressure_signal"] == PRESSURE_SIGNAL_SHORT).sum()
        ),
        "none": int(
            (df_trim["pressure_signal"] == PRESSURE_SIGNAL_NONE).sum()
        ),
    }

    summary: dict = {
        "total_capture_pct": _safe_float(total_capture_pct),
        "avg_daily_capture_pct": _safe_float(avg_daily_capture_pct),
        "sharpe_ratio": _safe_float(sharpe_ratio),
        "trigger_days": int(n_trigger),
        "wins": int(wins),
        "losses": int(losses),
        "p_value": None,
        "significant_95": None,
        "pressure_counts": pressure_counts,
        "timeframes": list(tf_list),
    }

    daily_out: list[dict] = []
    for _, row in df_trim.iterrows():
        daily_out.append({
            "date": (
                row["date"].strftime("%Y-%m-%d")
                if hasattr(row["date"], "strftime") else str(row["date"])
            ),
            "target_close": _safe_float(row["target_close"]),
            "target_return_pct": _safe_float(row["target_return_pct"]),
            "pressure_signal": str(row["pressure_signal"]),
            "timeframe_pressure_signals": dict(
                row["timeframe_pressure_signals"]
            ),
            "buy_count": int(row["buy_count"]),
            "short_count": int(row["short_count"]),
            "none_count": int(row["none_count"]),
            "missing_count": int(row["missing_count"]),
            "active_count": int(row["active_count"]),
            "available_count": int(row["available_count"]),
            "daily_capture_pct": _safe_float(row["daily_capture_pct"]),
            "cumulative_capture_pct": _safe_float(
                row["cumulative_capture_pct"]
            ),
            "is_trigger_day": bool(row["is_trigger_day"]),
        })

    return _ra.ResearchDayArtifact(
        artifact_version=_ra.ARTIFACT_VERSION,
        engine="trafficflow",
        target_ticker=str(artifact.target_ticker or "").strip().upper(),
        signal_source="",
        run_id=artifact_run_id_for_multitimeframe(artifact.run_id or ""),
        metric_basis=str(artifact.metric_basis or "Close"),
        persist_skip_bars=int(skip),
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        summary=summary,
        daily=daily_out,
        K=int(artifact.K) if artifact.K is not None else None,
        members=list(artifact.members or []),
        protocol_per_member=dict(artifact.protocol_per_member or {}),
        timeframes=list(tf_list),
    )


# ---------------------------------------------------------------------------
# Per-target sweep
# ---------------------------------------------------------------------------


def _engine_artifact_dir(
    artifact_root: Path, engine: str, ticker: str,
) -> Optional[Path]:
    if not artifact_root.exists() or not artifact_root.is_dir():
        return None
    base = artifact_root / engine
    if not base.exists() or not base.is_dir():
        return None
    safe = _filename_safe_ticker(ticker)
    real = str(ticker or "").strip().upper()
    for form in (real, safe):
        if not form:
            continue
        p = base / form
        if p.exists() and p.is_dir():
            return p
    return None


def _list_daily_k_artifacts(
    artifact_root: Path, ticker: str,
) -> list[Path]:
    """Find every saved daily-K TrafficFlow artifact for the
    ticker. Daily-K artifacts are produced by Phase 6D-1; their
    ``run_id`` matches the seed-run dir name with a ``__K<K>``
    suffix and they do NOT carry an ``__MTF`` suffix on disk.
    """
    ticker_dir = _engine_artifact_dir(
        artifact_root, "trafficflow", ticker,
    )
    if ticker_dir is None:
        return []
    out: list[Path] = []
    for p in sorted(ticker_dir.glob("*.research_day.json")):
        if MTF_SUFFIX in p.name:
            # Already an MTF artifact - skip as an input.
            continue
        out.append(p)
    return out


def _append_unique(issues: list[str], code: str) -> None:
    if code and code not in issues:
        issues.append(code)


def build_multitimeframe_bridge_artifacts_for_target(
    target_ticker: str,
    *,
    artifact_root: Optional[Path] = None,
    expected_k: Iterable[int] = DEFAULT_EXPECTED_K,
    timeframes: Iterable[str] = DEFAULT_TIMEFRAMES,
    write: bool = False,
    persist_skip_bars: Optional[int] = None,
) -> BuildResult:
    """Materialize one MTF TrafficFlow ``research_day_v1``
    artifact per saved daily-K artifact for the target.

    Read-only by default. ``write=True`` persists each successful
    artifact via ``research_artifacts.write_research_day_artifact``
    at the canonical path
    ``output/research_artifacts/trafficflow/<SAFE_TARGET>/<SAFE_RUN>__K<K>__MTF.research_day.json``.

    Returns a ``BuildResult`` with attempted / built / skipped K
    sets plus any issue codes. The function never raises for
    missing inputs - all failure modes are reported through
    ``issue_codes``.
    """
    t0 = time.perf_counter()
    expected_k_tuple = tuple(int(k) for k in expected_k)
    artifact_d = (
        Path(artifact_root) if artifact_root is not None
        else _default_artifact_root()
    )
    issues: list[str] = []

    daily_paths = _list_daily_k_artifacts(artifact_d, target_ticker)
    if not daily_paths:
        return BuildResult(
            target_ticker=target_ticker,
            attempted_k=(),
            built_k=(),
            skipped_k=(),
            artifact_paths=(),
            issue_codes=(ISSUE_NO_DAILY_K_ARTIFACTS,),
            elapsed_seconds=time.perf_counter() - t0,
        )

    attempted: list[int] = []
    built: list[int] = []
    skipped: list[int] = []
    paths: list[Path] = []
    seen_k: set[int] = set()

    wanted = set(expected_k_tuple)

    for path in daily_paths:
        try:
            artifact_in = _ra.read_research_day_artifact(path)
        except Exception:
            artifact_in = None
        if artifact_in is None:
            _append_unique(issues, ISSUE_INPUT_ARTIFACT_UNREADABLE)
            continue
        K = artifact_in.K
        if not isinstance(K, int):
            # Daily TrafficFlow artifacts ought to carry K; skip
            # the input silently if not - the readiness layer
            # surfaces the gap separately.
            continue
        if wanted and K not in wanted:
            continue
        if K in seen_k:
            # Multiple daily artifacts for the same K -> use the
            # first (sorted) one and skip duplicates.
            continue
        seen_k.add(K)
        attempted.append(K)
        try:
            artifact_out = build_multitimeframe_bridge_for_artifact(
                artifact_in,
                timeframes=timeframes,
                persist_skip_bars=persist_skip_bars,
            )
        except ValueError:
            skipped.append(K)
            _append_unique(issues, ISSUE_INPUT_ARTIFACT_NO_DAILY)
            continue
        except Exception:
            skipped.append(K)
            _append_unique(issues, ISSUE_ARTIFACT_WRITE_FAILED)
            continue

        if not write:
            built.append(K)
            continue

        out_path = _ra.artifact_path_for_trafficflow(
            target_ticker,
            artifact_run_id_for_multitimeframe(artifact_in.run_id or ""),
            base_dir=artifact_d,
        )
        if out_path is None:
            skipped.append(K)
            _append_unique(issues, ISSUE_ARTIFACT_WRITE_FAILED)
            continue
        try:
            written = _ra.write_research_day_artifact(
                artifact_out, out_path,
            )
        except Exception:
            skipped.append(K)
            _append_unique(issues, ISSUE_ARTIFACT_WRITE_FAILED)
            continue
        built.append(K)
        paths.append(Path(written))

    if wanted and not wanted.issubset(set(attempted)):
        _append_unique(issues, ISSUE_PARTIAL_K_COVERAGE)
    if skipped:
        _append_unique(issues, ISSUE_PARTIAL_K_COVERAGE)

    return BuildResult(
        target_ticker=target_ticker,
        attempted_k=tuple(attempted),
        built_k=tuple(built),
        skipped_k=tuple(skipped),
        artifact_paths=tuple(paths),
        issue_codes=tuple(issues),
        elapsed_seconds=time.perf_counter() - t0,
    )
