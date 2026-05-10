"""Phase 6C-5: Primary Signal Engine cache reader.

Read-only, offline helper that summarises PRJCT9's saved Spymaster
SMA signal history for one ticker. Powers the Phase 6 preview's
Primary Signal Engine first screen.

The reader walks ONE local file:

    project/cache/results/<TICKER>_precomputed_results.pkl

It does NOT call yfinance. It does NOT invoke OnePass /
ImpactSearch / StackBuilder / Confluence / TrafficFlow. It does
NOT trigger any artifact build. Looking at a ticker is a pure
saved-file read.

Display semantics: this module reports Spymaster cache numbers
(the same Sharpe / Total / capture path Spymaster's own dashboard
shows). It does NOT apply ImpactSearch's T-1 persist skip; that
is an artifact-format choice that lives in
``research_artifacts.build_impactsearch_day_artifact``. Spymaster
shows the unskipped daily capture path, and so do we.

Public surface:

    load_primary_signal_engine_payload(target, *, cache_dir=None,
                                       recent_n=15) -> dict
    parse_sma_pair(raw_active_pair) -> Optional[tuple[int, int]]
    UNAVAILABLE_REASONS                              # tuple[str, ...]
"""

from __future__ import annotations

import math
import pickle
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

# Soft imports - never block module load if these are missing.
try:
    import canonical_scoring as _cs
except Exception:  # pragma: no cover - canonical scoring is in-repo
    _cs = None

try:
    import research_artifacts as _ra
except Exception:  # pragma: no cover - in-repo helper
    _ra = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAYLOAD_SCHEMA_VERSION = "primary_signal_engine_payload_v1"
DEFAULT_RECENT_N = 15
DEFAULT_CHART_FLOOR = 1  # render the chart even with one row

# Reason codes (when ``available`` is False). Stable-string values
# so tests / UI can switch on them without translating.
REASON_NO_TICKER = "no_ticker"
REASON_CACHE_MISSING = "cache_missing"
REASON_CACHE_UNREADABLE = "cache_unreadable"
REASON_WRONG_CACHE_SHAPE = "wrong_cache_shape"
REASON_NO_CLOSE_COLUMN = "no_close_column"
REASON_NO_SIGNAL_DATA = "no_signal_data"
REASON_ALIGNMENT_MISMATCH = "active_pairs_alignment_mismatch"
REASON_EMPTY_AFTER_ALIGN = "no_signal_history"

UNAVAILABLE_REASONS: tuple[str, ...] = (
    REASON_NO_TICKER,
    REASON_CACHE_MISSING,
    REASON_CACHE_UNREADABLE,
    REASON_WRONG_CACHE_SHAPE,
    REASON_NO_CLOSE_COLUMN,
    REASON_NO_SIGNAL_DATA,
    REASON_ALIGNMENT_MISMATCH,
    REASON_EMPTY_AFTER_ALIGN,
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_cache_dir() -> Path:
    return _project_dir() / "cache" / "results"


def _ticker_forms(ticker: str) -> list[str]:
    """Real form first (e.g. ``^GSPC``) then filename-safe form
    (``_GSPC``). Mirrors the resolution rule in
    ``research_artifacts``."""
    real = str(ticker or "").strip().upper()
    forms: list[str] = []
    if real and real not in forms:
        forms.append(real)
    # Caret -> underscore for filename-safe.
    if real.startswith("^"):
        safe = "_" + real[1:]
        if safe not in forms:
            forms.append(safe)
    return forms


def _resolve_cache_path(
    target: str, cache_dir: Path,
) -> Optional[Path]:
    if not cache_dir.exists() or not cache_dir.is_dir():
        return None
    for form in _ticker_forms(target):
        p = cache_dir / f"{form}_precomputed_results.pkl"
        if p.exists() and p.is_file():
            return p
    return None


def _normalize_active_pair_to_signal(value: Any) -> str:
    """Map a Spymaster ``active_pairs`` entry to ``Buy`` / ``Short``
    / ``None``. Mirrors
    ``research_artifacts._normalize_active_pair_to_signal`` so the
    two paths agree on what counts as a Buy day."""
    if value is None:
        return "None"
    try:
        if isinstance(value, float) and math.isnan(value):
            return "None"
    except Exception:
        pass
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return "None"
    head = s.split()[0].lower() if s else ""
    if head == "buy":
        return "Buy"
    if head == "short":
        return "Short"
    return "None"


_SMA_PAIR_RE = re.compile(r"(\d+)\s*[,/]\s*(\d+)")


def parse_sma_pair(raw_active_pair: Any) -> Optional[tuple[int, int]]:
    """Parse the SMA pair out of a raw ``active_pairs`` entry such
    as ``"Buy 3,2"`` or ``"Short 1/5"``. Returns ``None`` for
    ``None``-typed entries or anything without a parseable pair."""
    if raw_active_pair is None:
        return None
    s = str(raw_active_pair).strip()
    if not s:
        return None
    m = _SMA_PAIR_RE.search(s)
    if m is None:
        return None
    try:
        a = int(m.group(1))
        b = int(m.group(2))
    except (TypeError, ValueError):
        return None
    if a == b:
        return None
    return a, b


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f


def _safe_int(v: Any) -> Optional[int]:
    f = _safe_float(v)
    if f is None:
        return None
    return int(round(f))


def _to_iso(d: Any) -> Optional[str]:
    if d is None:
        return None
    try:
        ts = pd.Timestamp(d)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Cache shape adapters
# ---------------------------------------------------------------------------


def _read_cache_pkl(path: Path) -> tuple[Optional[dict], Optional[str]]:
    """Read the PKL. Returns ``(obj, reason_or_none)``. ``obj`` is
    None on any error and ``reason`` carries the unavailable
    reason code. Never raises."""
    try:
        with path.open("rb") as fh:
            obj = pickle.load(fh)
    except Exception:
        return None, REASON_CACHE_UNREADABLE
    if not isinstance(obj, dict):
        return None, REASON_WRONG_CACHE_SHAPE
    return obj, None


def _close_series(cache_obj: Mapping[str, Any]) -> Optional[pd.Series]:
    """Extract the daily Close series from the cache. Returns None
    when the cache lacks a usable ``preprocessed_data`` DataFrame
    with a ``Close`` column."""
    pre = cache_obj.get("preprocessed_data")
    if pre is None or not isinstance(pre, pd.DataFrame) or pre.empty:
        return None
    if "Close" not in pre.columns:
        return None
    closes = pd.to_numeric(pre["Close"], errors="coerce")
    if closes.empty:
        return None
    closes = closes.copy()
    try:
        closes.index = pd.to_datetime(pre.index, errors="coerce")
    except Exception:
        return None
    closes = closes.dropna()
    if closes.empty:
        return None
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()
    return closes


def _aligned_signal_series(
    cache_obj: Mapping[str, Any],
    closes: pd.Series,
) -> tuple[Optional[pd.DataFrame], Optional[str]]:
    """Resolve the Buy / Short / None signal series for the cache's
    Close grid.

    Supports two cache shapes (mirrors
    ``research_artifacts._extract_member_signals_from_spymaster_cache``):

      1. ``primary_signals`` + ``dates`` (synthetic / signal-library
         shape).
      2. ``active_pairs`` aligned to ``preprocessed_data.index`` -
         either same length, or ``len(index) - 1`` (the historical
         Spymaster shape with no derived signal on the first row).

    Returns ``(df, None)`` on success where df has columns
    ``date``, ``signal`` (Buy / Short / None), ``raw_active_pair``
    indexed by date. Returns ``(None, reason)`` on failure with one
    of:
      * REASON_NO_SIGNAL_DATA - neither shape provides any data
      * REASON_ALIGNMENT_MISMATCH - active_pairs length neither
        matches index nor index-1
    """
    if closes is None or closes.empty:
        return None, REASON_NO_SIGNAL_DATA

    sigs = cache_obj.get("primary_signals")
    dates = cache_obj.get("dates")
    if (
        sigs is not None and dates is not None
        and len(sigs) == len(dates) and len(sigs) > 0
    ):
        try:
            df = pd.DataFrame({
                "date": pd.to_datetime(list(dates), errors="coerce"),
                "raw_active_pair": [str(s) for s in sigs],
            }).dropna(subset=["date"]).sort_values(
                "date",
            ).drop_duplicates(subset=["date"], keep="last").reset_index(
                drop=True,
            )
            if not df.empty:
                df["signal"] = df["raw_active_pair"].map(
                    _normalize_active_pair_to_signal,
                )
                # Constrain to the price-grid range so cumulative
                # capture aligns with the visible chart axis.
                df = df.set_index("date").reindex(closes.index).dropna(
                    subset=["raw_active_pair"],
                )
                if not df.empty:
                    df = df.reset_index().rename(columns={
                        "index": "date",
                    })
                    return df, None
        except Exception:
            pass

    ap = cache_obj.get("active_pairs")
    if ap is None:
        return None, REASON_NO_SIGNAL_DATA
    try:
        ap_list = list(ap)
    except TypeError:
        return None, REASON_NO_SIGNAL_DATA
    if not ap_list:
        return None, REASON_NO_SIGNAL_DATA

    n_idx = len(closes.index)
    n_ap = len(ap_list)
    if n_ap == n_idx:
        aligned_idx = closes.index
    elif n_ap == n_idx - 1:
        aligned_idx = closes.index[1:]
    else:
        return None, REASON_ALIGNMENT_MISMATCH

    try:
        df = pd.DataFrame({
            "date": aligned_idx,
            "raw_active_pair": [str(v) for v in ap_list],
        }).dropna(subset=["date"]).sort_values(
            "date",
        ).drop_duplicates(subset=["date"], keep="last").reset_index(
            drop=True,
        )
    except Exception:
        return None, REASON_NO_SIGNAL_DATA
    if df.empty:
        return None, REASON_NO_SIGNAL_DATA
    df["signal"] = df["raw_active_pair"].map(
        _normalize_active_pair_to_signal,
    )
    return df, None


# ---------------------------------------------------------------------------
# Cumulative capture + canonical metrics
# ---------------------------------------------------------------------------


def _build_chart_frame(
    closes: pd.Series, signals_df: pd.DataFrame,
) -> pd.DataFrame:
    """Return a per-day DataFrame indexed by date with columns
    ``close``, ``signal``, ``raw_active_pair``, ``daily_capture_pct``,
    ``cumulative_capture_pct``.

    Spymaster display semantics (NOT ImpactSearch artifact T-1):
      - daily_capture_pct on Buy day = +pct_change(close) * 100
      - daily_capture_pct on Short day = -pct_change(close) * 100
      - daily_capture_pct on None / unaligned day = 0
      - cumulative_capture_pct = daily_capture_pct.cumsum()
    """
    sig = signals_df.set_index("date")["signal"].reindex(closes.index)
    raw = signals_df.set_index("date")["raw_active_pair"].reindex(
        closes.index,
    )
    sig = sig.fillna("None").astype(str)
    raw = raw.fillna("")
    daily_return = closes.pct_change().fillna(0.0)
    sig_lower = sig.str.lower()
    daily_cap = pd.Series(0.0, index=closes.index)
    daily_cap[sig_lower.eq("buy")] = (
        daily_return[sig_lower.eq("buy")] * 100.0
    )
    daily_cap[sig_lower.eq("short")] = (
        -daily_return[sig_lower.eq("short")] * 100.0
    )
    cum = daily_cap.cumsum()
    out = pd.DataFrame({
        "close": closes.astype(float),
        "signal": sig,
        "raw_active_pair": raw,
        "daily_capture_pct": daily_cap,
        "cumulative_capture_pct": cum,
    })
    return out


def _canonical_score(chart_df: pd.DataFrame) -> Optional[Mapping[str, Any]]:
    """Score the chart frame via canonical_scoring.score_signals.
    Returns a dict slice (sharpe, total, signal_days, win_rate)
    suitable for the payload, or None when canonical_scoring is
    unavailable or the chart frame is empty.

    Falls back to a hand-rolled compute on the same daily-capture
    series so the payload stays useful even in stripped test
    environments where canonical_scoring fails to import."""
    if chart_df is None or chart_df.empty:
        return None
    if _cs is not None:
        try:
            sigs = chart_df["signal"]
            returns = chart_df["close"].pct_change().fillna(0.0)
            score = _cs.score_signals(sigs, returns)
            return {
                "total_capture_pct": _safe_float(score.total_capture),
                "sharpe_ratio": _safe_float(score.sharpe),
                "signal_days": _safe_int(score.trigger_days),
                "win_rate_pct": _safe_float(score.win_rate),
                "wins": _safe_int(score.wins),
                "losses": _safe_int(score.losses),
            }
        except Exception:
            pass
    # Manual fallback: same Buy/Short -> daily capture rule, plain
    # arithmetic Sharpe (ddof=1, annualized 252) so the displayed
    # number remains meaningful when canonical_scoring isn't
    # importable.
    sig_lower = chart_df["signal"].astype(str).str.lower()
    trigger_mask = sig_lower.isin({"buy", "short"})
    triggers = chart_df.loc[trigger_mask, "daily_capture_pct"]
    n_trig = int(trigger_mask.sum())
    if n_trig == 0:
        return {
            "total_capture_pct": 0.0,
            "sharpe_ratio": 0.0,
            "signal_days": 0,
            "win_rate_pct": 0.0,
            "wins": 0,
            "losses": 0,
        }
    avg = float(triggers.mean())
    total = float(triggers.sum())
    wins = int((triggers > 0).sum())
    losses = n_trig - wins
    if n_trig > 1:
        std = float(triggers.std(ddof=1))
    else:
        std = 0.0
    sharpe = 0.0
    if std > 0:
        ann_ret = avg * 252.0
        ann_std = std * math.sqrt(252.0)
        sharpe = (ann_ret - 5.0) / ann_std
    return {
        "total_capture_pct": _safe_float(total),
        "sharpe_ratio": _safe_float(sharpe),
        "signal_days": int(n_trig),
        "win_rate_pct": _safe_float(
            (wins / n_trig * 100.0) if n_trig else 0.0,
        ),
        "wins": int(wins),
        "losses": int(losses),
    }


# ---------------------------------------------------------------------------
# Public payload builder
# ---------------------------------------------------------------------------


def _unavailable(target: str, reason: str, **extra) -> dict:
    out = {
        "schema": PAYLOAD_SCHEMA_VERSION,
        "ticker": str(target or "").strip().upper(),
        "available": False,
        "reason": reason,
        "date_range": None,
        "current_signal": None,
        "current_active_pair_raw": None,
        "current_sma_pair": None,
        "total_capture_pct": None,
        "sharpe_ratio": None,
        "signal_days": None,
        "win_rate_pct": None,
        "latest_close": None,
        "chart_rows": [],
        "recent_rows": [],
        "metric_basis": (
            "Spymaster cache (preprocessed_data + active_pairs)"
        ),
    }
    out.update(extra)
    return out


def load_primary_signal_engine_payload(
    target: str,
    *,
    cache_dir: Optional[Path] = None,
    recent_n: int = DEFAULT_RECENT_N,
) -> dict:
    """Read the Spymaster cache for ``target`` and return the
    Primary Signal Engine payload.

    Always returns a payload dict with ``schema`` set to
    ``primary_signal_engine_payload_v1``. ``available`` is False
    on any failure path; the ``reason`` field tells the UI
    which fallback copy to render.

    Strictly read-only and offline. Never invokes a live engine
    or touches the network.
    """
    target_clean = str(target or "").strip().upper()
    if not target_clean:
        return _unavailable(target_clean, REASON_NO_TICKER)

    cdir = Path(cache_dir) if cache_dir else _default_cache_dir()
    path = _resolve_cache_path(target_clean, cdir)
    if path is None:
        return _unavailable(target_clean, REASON_CACHE_MISSING)

    obj, err = _read_cache_pkl(path)
    if obj is None:
        return _unavailable(target_clean, err or REASON_CACHE_UNREADABLE)

    closes = _close_series(obj)
    if closes is None or closes.empty:
        return _unavailable(target_clean, REASON_NO_CLOSE_COLUMN)

    signals_df, sig_err = _aligned_signal_series(obj, closes)
    if signals_df is None:
        return _unavailable(
            target_clean, sig_err or REASON_NO_SIGNAL_DATA,
        )
    if signals_df.empty:
        return _unavailable(target_clean, REASON_EMPTY_AFTER_ALIGN)

    chart_df = _build_chart_frame(closes, signals_df)
    if chart_df.empty:
        return _unavailable(target_clean, REASON_EMPTY_AFTER_ALIGN)

    metrics = _canonical_score(chart_df) or {}

    # Walk the trailing rows for current state. Use the last row
    # whose raw_active_pair is non-empty so a tail of "None" rows
    # does not clobber the visible "current" state. Spymaster's own
    # dashboard surfaces the freshest non-empty pair, and we mirror
    # that here.
    current_signal = None
    current_raw = None
    current_pair = None
    for _, row in chart_df[::-1].iterrows():
        raw = row.get("raw_active_pair")
        if raw is None or str(raw).strip() == "":
            continue
        current_raw = str(raw)
        current_signal = str(row.get("signal") or "None")
        current_pair = parse_sma_pair(current_raw)
        break
    if current_signal is None:
        # No non-empty pair: fall back to the very last row's signal
        # (will be "None" by construction).
        current_signal = str(
            chart_df["signal"].iloc[-1]
            if len(chart_df) else "None"
        )
        current_raw = str(
            chart_df["raw_active_pair"].iloc[-1]
            if len(chart_df) else ""
        )
        current_pair = parse_sma_pair(current_raw)

    chart_rows: list[dict] = []
    for date, row in chart_df.iterrows():
        chart_rows.append({
            "date": _to_iso(date),
            "close": _safe_float(row["close"]),
            "signal": str(row["signal"]),
            "raw_active_pair": str(row["raw_active_pair"] or ""),
            "daily_capture_pct": _safe_float(
                row["daily_capture_pct"],
            ),
            "cumulative_capture_pct": _safe_float(
                row["cumulative_capture_pct"],
            ),
        })

    n_recent = max(1, int(recent_n))
    recent_rows = chart_rows[-n_recent:][::-1]

    return {
        "schema": PAYLOAD_SCHEMA_VERSION,
        "ticker": target_clean,
        "available": True,
        "reason": None,
        "date_range": {
            "start": chart_rows[0]["date"] if chart_rows else None,
            "end": chart_rows[-1]["date"] if chart_rows else None,
        },
        "current_signal": current_signal,
        "current_active_pair_raw": current_raw,
        "current_sma_pair": (
            list(current_pair) if current_pair else None
        ),
        "total_capture_pct": metrics.get("total_capture_pct"),
        "sharpe_ratio": metrics.get("sharpe_ratio"),
        "signal_days": metrics.get("signal_days"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "latest_close": _safe_float(closes.iloc[-1]) if len(closes) else None,
        "chart_rows": chart_rows,
        "recent_rows": recent_rows,
        "metric_basis": (
            "Spymaster cache (preprocessed_data + active_pairs)"
        ),
    }
