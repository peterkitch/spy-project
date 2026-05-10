"""Phase 6A local interactive research engine preview.

A standalone Dash workbench that lets a user pick a target/secondary
ticker, choose primaries, browse existing ImpactSearch XLSX outputs (or
attempt a bounded live preview), inspect a selected result, and read
plain-language validation caveats. Local-preview only - no auth, no
cloud, no provider migration.

This module wraps existing engine outputs read-only. It does NOT
modify, re-run, or refactor production engines. Run with:

    python phase6_research_preview.py

The Dash server binds to 127.0.0.1:8060 by default. Override with the
PRJCT9_PREVIEW_PORT environment variable.
"""

from __future__ import annotations

import io
import json
import os
import pickle
import re
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


# Make the global_ticker_library / signal_library shared loaders find
# their master_tickers list regardless of the cwd the launcher used.
# Without this, booting from the repo root prints
#   "Could not load master tickers from
#    global_ticker_library/data/master_tickers.txt"
# during the live "Test 10 signal sources" path. The shared loader at
# signal_library/shared_symbols.py honors YF_MASTER_TICKERS_PATH first,
# so set the absolute project-relative path here at import time.
def _ensure_master_tickers_env() -> None:
    if os.environ.get("YF_MASTER_TICKERS_PATH"):
        return
    project_dir = Path(__file__).resolve().parent
    candidate = (
        project_dir / "global_ticker_library" / "data"
        / "master_tickers.txt"
    )
    if candidate.exists() and candidate.is_file():
        os.environ["YF_MASTER_TICKERS_PATH"] = str(candidate)


_ensure_master_tickers_env()

import numpy as np
import pandas as pd

# Dash imports are deferred so the helper module is importable for tests
# in environments where Dash is missing. The actual app boot code at the
# bottom of the file imports lazily.

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TARGET = "SPY"
DEFAULT_PORT = int(os.environ.get("PRJCT9_PREVIEW_PORT", "8060"))
MAX_PRIMARIES_LIVE = 10

PRJCT9_BLACK = "#000000"
PRJCT9_GREEN = "#80ff00"
PRJCT9_DIM = "#1a1a1a"
PRJCT9_TEXT = "#e6e6e6"
PRJCT9_MUTED = "#888888"
PRJCT9_BORDER = "#2a2a2a"
PRJCT9_RED = "#ff5050"

# Display column name -> source column name (in the XLSX). When the
# source column is missing from the sheet, the display column becomes
# empty/None at normalize time.
DISPLAY_COLUMNS: list[tuple[str, str]] = [
    ("Primary Ticker", "Primary Ticker"),
    ("Secondary Ticker", "Secondary Ticker"),
    ("Total Capture (%)", "Total Capture (%)"),
    ("Avg Daily Capture (%)", "Avg Daily Capture (%)"),
    ("Sharpe", "Sharpe Ratio"),
    ("Trigger Days", "Trigger Days"),
    ("Wins", "Wins"),
    ("Losses", "Losses"),
    ("P-Value", "p-Value"),
    ("Significant 95%", "Significant 95%"),
    ("Data Source", "Data Source"),
    ("Result Mode", "Result Mode"),
]
NUMERIC_DISPLAY_COLUMNS = {
    "Total Capture (%)",
    "Avg Daily Capture (%)",
    "Sharpe",
    "Trigger Days",
    "Wins",
    "Losses",
    "P-Value",
}

# Preset universes (all small / curated; never the full 73K).
PRIMARY_UNIVERSE_PRESETS: dict[str, list[str]] = {
    "Mega Cap 10": [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
        "META", "TSLA", "BRK-B", "AVGO", "JPM",
    ],
    "Sector ETFs": [
        "XLK", "XLF", "XLV", "XLY", "XLP",
        "XLE", "XLI", "XLU", "XLRE", "XLB",
    ],
    "Broad Index ETFs": [
        "SPY", "QQQ", "DIA", "IWM", "VTI",
        "VOO", "EFA", "EEM", "AGG", "TLT",
    ],
    "Custom": [],
}

OUTPUT_SUBDIR = Path("output") / "impactsearch"


# ---------------------------------------------------------------------------
# Helpers (pure, importable, testable without Dash)
# ---------------------------------------------------------------------------


def _normalize_ticker_for_filename(ticker: str) -> str:
    """Convert a user-facing ticker to its on-disk filename stem.

    ``SPY`` -> ``SPY``; ``^GSPC`` -> ``_GSPC``; ``^IXIC`` -> ``_IXIC``.
    Trims whitespace and uppercases. Empty / None returns ``""``.
    """
    if ticker is None:
        return ""
    t = str(ticker).strip().upper()
    if not t:
        return ""
    if t.startswith("^"):
        return "_" + t[1:]
    return t


def _candidate_xlsx_filenames_for(ticker: str) -> list[str]:
    """Return the ordered list of plausible XLSX filenames for a ticker.

    Real on-disk files in this repo use the ``^`` prefix for index
    tickers (e.g. ``^GSPC_analysis.xlsx``); the spec's normalization
    helper converts ``^`` -> ``_`` for portable storage. Both shapes
    are tolerated in the loader.
    """
    user = (ticker or "").strip().upper()
    if not user:
        return []
    candidates: list[str] = []

    def _add(stem: str) -> None:
        name = f"{stem}_analysis.xlsx"
        if name not in candidates:
            candidates.append(name)

    _add(user)
    if user.startswith("^"):
        _add("_" + user[1:])
        _add(user[1:])
    elif user.startswith("_"):
        _add("^" + user[1:])
        _add(user[1:])
    return candidates


def _discover_impactsearch_outputs(output_dir: Path) -> list[Path]:
    """Return sorted list of ``*_analysis.xlsx`` files under ``output_dir``.

    Returns ``[]`` when the directory does not exist or contains no
    matching files. Excludes any non-``.xlsx`` files. Sort order is
    case-sensitive lexicographic so the UI list is deterministic.
    """
    if output_dir is None:
        return []
    p = Path(output_dir)
    if not p.exists() or not p.is_dir():
        return []
    matches = [
        path for path in p.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".xlsx"
        and path.stem.endswith("_analysis")
    ]
    # Sort case-insensitively by filename so the UI list is platform-stable
    # (Windows Path comparison is case-insensitive while raw str sort is
    # case-sensitive; lower-case keying picks one shared order.)
    return sorted(matches, key=lambda x: x.name.lower())


def _load_impactsearch_xlsx(path: Path) -> pd.DataFrame:
    """Read the first sheet of an ImpactSearch analysis XLSX.

    Returns an empty DataFrame on read failure (the UI surfaces this as
    a Run Log error). Does NOT modify the file on disk. The caller
    should normalize via ``_normalize_results_frame``.
    """
    try:
        return pd.read_excel(path, engine="openpyxl")
    except Exception:
        try:
            return pd.read_excel(path)
        except Exception:
            return pd.DataFrame()


def _coerce_numeric(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        if not v or v.upper() in {"N/A", "NA", "NONE", "NULL", "-"}:
            return None
        try:
            return float(v)
        except ValueError:
            return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f


def _normalize_results_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Project the source frame onto the display schema.

    Preserves known columns by mapping source-column-name to display
    name. Coerces numeric metric columns where possible; non-numeric
    cells become None. Tolerates missing source columns by adding the
    display column with None values. Extra source columns are dropped
    (they would clutter the table without adding decision value).
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=[disp for disp, _ in DISPLAY_COLUMNS])

    out = pd.DataFrame()
    src_cols = {str(c).strip(): c for c in df.columns}

    def _resolve(source: str) -> Optional[Any]:
        if source in src_cols:
            return df[src_cols[source]]
        # case-insensitive fallback
        lower_map = {k.lower(): k for k in src_cols}
        if source.lower() in lower_map:
            return df[src_cols[lower_map[source.lower()]]]
        return None

    for display, source in DISPLAY_COLUMNS:
        col = _resolve(source)
        if col is None:
            out[display] = pd.Series([None] * len(df), dtype=object)
            continue
        if display in NUMERIC_DISPLAY_COLUMNS:
            out[display] = col.map(_coerce_numeric)
        else:
            # Cast to string to keep DataTable rendering predictable
            out[display] = col.astype(object).where(col.notna(), None)

    return out


def _primary_universe_from_preset(
    preset: str, custom_text: str, *, live_mode: bool = False
) -> list[str]:
    """Resolve the active primary ticker universe from preset + custom text.

    Returns deduplicated, uppercased tickers. ``Custom`` parses
    comma- or newline-separated text. Other presets ignore custom_text
    and use the locked preset list. In live mode the result is hard-
    capped at ``MAX_PRIMARIES_LIVE``; in browse mode the cap does not
    apply (browsing existing XLSX outputs is read-only).
    """
    seen: set[str] = set()
    ordered: list[str] = []

    def _push(token: str) -> None:
        t = (token or "").strip().upper()
        if not t or t in seen:
            return
        seen.add(t)
        ordered.append(t)

    preset_key = (preset or "").strip()
    if preset_key in PRIMARY_UNIVERSE_PRESETS and preset_key != "Custom":
        for t in PRIMARY_UNIVERSE_PRESETS[preset_key]:
            _push(t)
    else:
        text = custom_text or ""
        # Tolerate commas, semicolons, newlines, spaces
        for token in re.split(r"[,;\s]+", text):
            _push(token)

    if live_mode and len(ordered) > MAX_PRIMARIES_LIVE:
        ordered = ordered[:MAX_PRIMARIES_LIVE]
    return ordered


FRAGILE_TRIGGER_DAYS_THRESHOLD = 20
LOW_SAMPLE_TRIGGER_DAYS_THRESHOLD = 30


def _result_summary(df: pd.DataFrame) -> dict:
    """Compute a small summary dict used by the Overview / Result Detail tabs.

    Empty / None frames give safe zeros / Nones. Numeric stats are
    computed only on rows where the relevant metric is finite.
    """
    if df is None or df.empty:
        return {
            "rows": 0,
            "best_total_capture": None,
            "best_total_capture_primary": None,
            "median_sharpe": None,
            "trigger_days_min": None,
            "trigger_days_max": None,
            "significant_95_count": 0,
            "fragile_count": 0,
            "available_columns": [],
        }

    cols = list(df.columns)

    def _finite_series(col: str) -> pd.Series:
        if col not in df.columns:
            return pd.Series(dtype=float)
        return pd.to_numeric(df[col], errors="coerce").dropna()

    total_capture = _finite_series("Total Capture (%)")
    sharpe = _finite_series("Sharpe")
    trigger = _finite_series("Trigger Days")

    best_idx = (
        total_capture.idxmax() if len(total_capture) else None
    )
    best_value = float(total_capture.loc[best_idx]) if best_idx is not None else None
    best_primary = (
        str(df.loc[best_idx, "Primary Ticker"]) if (best_idx is not None and "Primary Ticker" in df.columns) else None
    )

    sig95_count = 0
    if "Significant 95%" in df.columns:
        sig95_count = int(
            df["Significant 95%"].astype(str).str.strip().str.upper().eq("YES").sum()
        )

    fragile_count = 0
    if "Trigger Days" in df.columns:
        td_numeric = pd.to_numeric(df["Trigger Days"], errors="coerce")
        fragile_count = int((td_numeric < FRAGILE_TRIGGER_DAYS_THRESHOLD).sum())

    return {
        "rows": int(len(df)),
        "best_total_capture": best_value,
        "best_total_capture_primary": best_primary,
        "median_sharpe": float(sharpe.median()) if len(sharpe) else None,
        "trigger_days_min": int(trigger.min()) if len(trigger) else None,
        "trigger_days_max": int(trigger.max()) if len(trigger) else None,
        "significant_95_count": sig95_count,
        "fragile_count": fragile_count,
        "available_columns": cols,
    }


def _evidence_label(row: Mapping[str, Any]) -> str:
    """UI-only evidence label derived from existing columns.

    Plain-English presentational helper for the Overview / Result
    Detail tabs. NOT scoring. NOT written into source files.

    Categories (in priority order):
      - "Strong historical sample" if Significant 95% == Yes AND
        Signal Days >= LOW_SAMPLE_TRIGGER_DAYS_THRESHOLD (30)
      - "Interesting, but small sample" if Significant 95% == Yes AND
        Signal Days < 30
      - "Too few signal days" if Signal Days < FRAGILE_TRIGGER_DAYS_THRESHOLD (20)
      - "Exploratory" otherwise
    """
    if not isinstance(row, Mapping):
        return "Exploratory"
    sig_raw = row.get("Significant 95%")
    sig_yes = (
        isinstance(sig_raw, str)
        and sig_raw.strip().upper() == "YES"
    )
    td_raw = row.get("Trigger Days")
    try:
        td = float(td_raw) if td_raw is not None and td_raw != "" else None
        if td is not None and not np.isfinite(td):
            td = None
    except (TypeError, ValueError):
        td = None

    if sig_yes:
        if td is not None and td >= LOW_SAMPLE_TRIGGER_DAYS_THRESHOLD:
            return "Strong historical sample"
        return "Interesting, but small sample"
    if td is not None and td < FRAGILE_TRIGGER_DAYS_THRESHOLD:
        return "Too few signal days"
    return "Exploratory"


def _overview_interesting_rows(df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """Return up to ``top_n`` rows ordered by a practical research
    sort:

      1. Significant 95% == Yes first (when the column is present)
      2. Sharpe descending
      3. Trigger Days descending
      4. Total Capture (%) descending

    Tolerates missing columns. Empty / None inputs return an empty
    DataFrame with the original columns + a synthetic
    "Evidence" column so the Overview table can render either way.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=list(df.columns) + ["Evidence"]) if df is not None else pd.DataFrame()

    work = df.copy()
    if "Significant 95%" in work.columns:
        work["__sig"] = work["Significant 95%"].astype(str).str.strip().str.upper().eq("YES").astype(int)
    else:
        work["__sig"] = 0
    for col in ("Sharpe", "Trigger Days", "Total Capture (%)"):
        if col in work.columns:
            work[f"__{col}"] = pd.to_numeric(work[col], errors="coerce")
        else:
            work[f"__{col}"] = float("nan")

    work = work.sort_values(
        by=["__sig", "__Sharpe", "__Trigger Days", "__Total Capture (%)"],
        ascending=[False, False, False, False],
        na_position="last",
    ).head(top_n)
    work = work.drop(
        columns=[c for c in work.columns if c.startswith("__")],
        errors="ignore",
    )
    work["Evidence"] = work.apply(
        lambda r: _evidence_label(r.to_dict()), axis=1
    )
    return work.reset_index(drop=True)


def _format_run_log_start(
    target: str, primaries: list[str], engine_ready: bool, ts: str,
) -> list[str]:
    """Activity entries when the live signal-source test begins."""
    primaries_label = ", ".join(primaries) if primaries else "(none)"
    return [
        f"[{ts}] live test started",
        f"[{ts}]   ticker studied: {target}",
        f"[{ts}]   signal sources: {primaries_label}",
        f"[{ts}]   limit: up to {MAX_PRIMARIES_LIVE} signal sources",
        f"[{ts}]   engine: {'ready' if engine_ready else 'not ready'}",
    ]


def _format_run_log_success(
    target: str, n_rows: int, elapsed_seconds: float,
    fastpath_count: int, ts: str,
) -> list[str]:
    """Activity entries for a successful live signal-source test."""
    return [
        f"[{ts}] Live test finished.",
        f"[{ts}]   ticker studied: {target}",
        f"[{ts}]   rows: {n_rows}",
        f"[{ts}]   elapsed: {elapsed_seconds:.1f}s",
    ]


def _format_run_log_failure(
    elapsed_seconds: Optional[float], error: Optional[str], ts: str,
) -> list[str]:
    """Activity entries for a failed live signal-source test."""
    elapsed_str = (
        f"{elapsed_seconds:.1f}s" if elapsed_seconds is not None else "?"
    )
    err_str = error or "unknown error"
    return [
        f"[{ts}] live test failed after {elapsed_str}",
        f"[{ts}]   reason: {err_str}",
        f"[{ts}]   suggestion: try Open saved ticker study, or "
        "restart the launcher.",
    ]


def _count_fastpath_rows(rows: list[dict]) -> int:
    """Count how many quick-study rows came from cached signal libraries.

    This is internal-only telemetry; the Run Log no longer mentions
    FastPath in user-facing copy. Kept for tests and console logs.
    """
    if not rows:
        return 0
    n = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        ds = r.get("Data Source") or r.get("data_source") or ""
        if isinstance(ds, str) and ds.strip().upper() == "FASTPATH":
            n += 1
    return n


def _onepass_output_dir(project_dir: Optional[Path] = None) -> Path:
    """Return the canonical local OnePass output directory.

    The default lives next to phase6_research_preview.py at
    ``output/onepass/``. Callers can override ``project_dir`` so tests
    can point at a tmp_path fixture without touching the real tree.
    """
    base = (project_dir if project_dir is not None
            else Path(__file__).resolve().parent)
    return Path(base) / "output" / "onepass"


def _discover_onepass_outputs(
    project_dir: Optional[Path] = None,
) -> list[Path]:
    """Find saved OnePass output files. Looks for ``onepass*.xlsx`` in
    the OnePass output dir, filters out manifests/sidecars, and
    returns paths sorted newest-first by mtime. Empty list when the
    directory is missing or contains no candidates."""
    out_dir = _onepass_output_dir(project_dir)
    if not out_dir.exists() or not out_dir.is_dir():
        return []
    candidates: list[Path] = []
    try:
        for entry in out_dir.iterdir():
            if not entry.is_file():
                continue
            name = entry.name.lower()
            if not (name.startswith("onepass") and name.endswith(".xlsx")):
                continue
            if name.endswith(".manifest.json") or "._" in name:
                continue
            candidates.append(entry)
    except OSError:
        return []
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates


def _load_onepass_summary(path: Path, top_n: int = 5) -> Optional[dict]:
    """Read a saved OnePass XLSX into a small summary dict.

    Returns ``{'rows': N, 'columns': [...], 'top': DataFrame}`` or
    None when the file is missing/unreadable. The ``top`` DataFrame
    is the ``top_n`` rows ranked by ``Total Capture (%)`` descending,
    with a compact column subset for the cockpit Market Scan panel.
    """
    if path is None or not Path(path).exists():
        return None
    try:
        df = pd.read_excel(path, engine="openpyxl")
    except Exception:
        return None
    if df is None or df.empty:
        return {"rows": 0, "columns": [], "top": pd.DataFrame()}
    rows = int(len(df))
    columns = [str(c) for c in df.columns]
    rank_col = (
        "Total Capture (%)" if "Total Capture (%)" in df.columns
        else None
    )
    if rank_col is not None:
        try:
            df_sorted = df.sort_values(rank_col, ascending=False)
        except Exception:
            df_sorted = df
    else:
        df_sorted = df
    keep = [
        c for c in (
            "Primary Ticker",
            "Total Capture (%)",
            "Sharpe Ratio",
            "Trigger Days",
            "Significant 95%",
        ) if c in df_sorted.columns
    ]
    top = df_sorted.head(int(top_n))
    if keep:
        top = top.reindex(columns=keep)
    return {
        "rows": rows,
        "columns": columns,
        "top": top.reset_index(drop=True),
    }


def _local_price_series_for_target(
    target: str,
    cache_dir: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """Load a daily Close price series for ``target`` from local
    Spymaster cache, never touching the network.

    Looks for ``cache/results/<TARGET>_precomputed_results.pkl`` (the
    standalone Spymaster cache). When present, the file's
    ``preprocessed_data`` DataFrame is indexed by Date and has a Close
    column. Returns a 2-column DataFrame with columns ``date`` and
    ``close`` (date is a python date / pandas Timestamp), or None when
    no usable local series is found.

    Failure modes (missing file, unpickle error, missing column) all
    return None silently — callers render an honest 'not available'
    line instead of erroring the page.
    """
    if not target or not isinstance(target, str):
        return None
    safe = target.strip().upper()
    if not safe:
        return None
    base = cache_dir or "cache/results"
    path = Path(base) / f"{safe}_precomputed_results.pkl"
    if not path.exists() or not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            obj = pickle.load(fh)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    df = obj.get("preprocessed_data")
    if df is None:
        return None
    try:
        if "Close" not in df.columns:
            return None
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
    except Exception:
        return None
    if closes.empty:
        return None
    out = pd.DataFrame({
        "date": closes.index,
        "close": closes.values,
    })
    return out


def _signal_library_stable_path(
    signal_source: str,
    sig_lib_dir: Optional[str] = None,
    suffix: str = "",
) -> Optional[Path]:
    """Path to a saved stable signal-library PKL for ``signal_source``.

    ``suffix`` is the timeframe marker used by the confluence stack
    (e.g. ``""`` for daily, ``"_1wk"``, ``"_1mo"``, ``"_3mo"``,
    ``"_1y"``). Returns the path even if the file does not exist;
    callers should test ``.exists()``.
    """
    if not signal_source or not isinstance(signal_source, str):
        return None
    safe = signal_source.strip().upper()
    if not safe:
        return None
    base = sig_lib_dir or "signal_library/data/stable"
    return Path(base) / f"{safe}_stable_v1_0_0{suffix}.pkl"


def _load_stable_signal_library(
    signal_source: str,
    sig_lib_dir: Optional[str] = None,
    suffix: str = "",
) -> Optional[dict]:
    """Read a stable signal-library PKL into a dict. Returns None for
    missing/corrupt/wrong-shape files. Used by the cumulative-capture
    helper (daily only) and the confluence-status helper (multiple
    suffixes)."""
    path = _signal_library_stable_path(signal_source, sig_lib_dir, suffix)
    if path is None or not path.exists() or not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            obj = pickle.load(fh)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


# ImpactSearch daily-capture T-1 persistence policy: drop the most
# recent N bars from the trimmed grid before the cumulative sum so
# the chart final value reconciles with the saved Total Capture (%).
# Mirrors ``impactsearch.PERSIST_SKIP_BARS`` (1).
_PERSIST_SKIP_BARS_DEFAULT = 1


def _selected_pattern_cumulative_capture(
    signal_source: str,
    target: str,
    sig_lib_dir: Optional[str] = None,
    cache_dir: Optional[str] = None,
    persist_skip_bars: Optional[int] = None,
) -> Optional[pd.DataFrame]:
    """Reconstruct the cumulative capture series for the selected
    (signal_source, target) pair from saved local data.

    Mirrors the daily-capture mapping that ImpactSearch's
    ``calculate_metrics_from_signals`` uses:

      * Buy   day -> daily_capture = +pct_change(target Close) * 100
      * Short day -> daily_capture = -pct_change(target Close) * 100
      * None  / Cash / missing -> 0

    Applies the same T-1 persistence skip ImpactSearch enforces: the
    final ``persist_skip_bars`` (default 1) bars of the aligned grid
    are dropped before the cumulative sum so the chart final value
    reconciles with the saved Total Capture (%) in the ranked output
    table. ``persist_skip_bars=0`` disables the skip (useful for
    tests that hand-craft tiny fixtures).

    Returns a DataFrame with columns ``date``, ``signal``,
    ``daily_capture``, ``cum_capture``. Returns None when either the
    signal library or the target cache is missing/unreadable.

    Strictly read-only and offline. No network access. No engine
    import. Used both by the live cockpit chart and by the test
    suite (which feeds tmp_path fixtures).
    """
    if not signal_source or not target:
        return None
    if not isinstance(signal_source, str) or not isinstance(target, str):
        return None

    lib = _load_stable_signal_library(signal_source, sig_lib_dir)
    if lib is None:
        return None
    sigs = lib.get("primary_signals")
    dates = lib.get("dates")
    if sigs is None or dates is None:
        return None
    try:
        sigs_arr = list(sigs)
        dates_arr = pd.to_datetime(list(dates), errors="coerce")
    except Exception:
        return None
    if len(sigs_arr) == 0 or len(sigs_arr) != len(dates_arr):
        return None
    sig_df = pd.DataFrame({
        "signal": [str(s) for s in sigs_arr],
    }, index=dates_arr)
    sig_df = sig_df[sig_df.index.notna()]
    if sig_df.empty:
        return None

    prices = _local_price_series_for_target(target, cache_dir=cache_dir)
    if prices is None or prices.empty:
        return None
    try:
        prices = prices.copy()
        prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
        prices = prices.dropna(subset=["date"])
        prices = prices.sort_values("date").drop_duplicates(
            subset=["date"], keep="last",
        )
        prices = prices.set_index("date")
    except Exception:
        return None
    if prices.empty:
        return None

    rets = pd.to_numeric(prices["close"], errors="coerce").pct_change()
    rets.name = "ret"
    aligned = rets.to_frame().join(sig_df, how="inner")
    if aligned.empty:
        return None
    aligned["signal"] = aligned["signal"].astype(str)
    sig_norm = aligned["signal"].str.strip().str.lower()
    daily = pd.Series(0.0, index=aligned.index)
    daily = daily.where(sig_norm.ne("buy"), aligned["ret"] * 100.0)
    daily = daily.where(sig_norm.ne("short"), -aligned["ret"] * 100.0)
    daily = daily.fillna(0.0)

    # ImpactSearch T-1 persistence skip: drop the trailing N bars
    # before the cumulative sum. Default mirrors
    # ``impactsearch.PERSIST_SKIP_BARS`` (1).
    skip = (
        _PERSIST_SKIP_BARS_DEFAULT if persist_skip_bars is None
        else int(persist_skip_bars)
    )
    if skip and skip > 0 and len(daily) > skip:
        daily = daily.iloc[:-skip]
        signal_series = aligned["signal"].iloc[:-skip]
        index = aligned.index[:-skip]
    else:
        signal_series = aligned["signal"]
        index = aligned.index
    cum = daily.cumsum()
    out = pd.DataFrame({
        "date": index,
        "signal": signal_series.values,
        "daily_capture": daily.values,
        "cum_capture": cum.values,
    })
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Phase 6B-1: canonical research_day_v1 artifact integration
# ---------------------------------------------------------------------------


def _read_research_day_artifact_for_pair(
    signal_source: str,
    target: str,
):
    """Return the saved canonical day-by-day artifact for the
    (signal source, target) pair, or None when none exists / the
    file is missing or corrupt. Wraps
    ``research_artifacts.read_research_day_artifact`` so callers
    don't have to import the artifact module directly."""
    try:
        import research_artifacts as ra
    except Exception:
        return None
    path = ra.artifact_path_for_impactsearch(target, signal_source)
    if path is None:
        return None
    return ra.read_research_day_artifact(path)


def _build_research_day_artifact_for_pair(
    signal_source: str,
    target: str,
    summary_overrides: Optional[Mapping[str, Any]] = None,
) -> Optional[Path]:
    """Build + persist a canonical research_day_v1 artifact for the
    (signal source, target) pair from local saved data. Returns the
    on-disk path on success, or None when the source PKLs are
    missing / corrupt. Strictly local; no network."""
    try:
        import research_artifacts as ra
    except Exception:
        return None
    artifact = ra.build_impactsearch_day_artifact_from_local(
        target, signal_source,
        summary_overrides=summary_overrides,
    )
    if artifact is None:
        return None
    path = ra.artifact_path_for_impactsearch(target, signal_source)
    if path is None:
        return None
    try:
        return ra.write_research_day_artifact(artifact, path)
    except Exception:
        return None


def _read_stack_artifact_for_run(
    target: str,
    run_id: str,
    K: int,
):
    """Phase 6B-2: read the saved stack day-by-day artifact for
    (target, run_id, K) when present. Returns None for missing /
    corrupt files."""
    try:
        import research_artifacts as ra
    except Exception:
        return None
    path = ra.artifact_path_for_stackbuilder(target, run_id, K)
    if path is None:
        return None
    return ra.read_research_day_artifact(path)


def _build_stack_artifact_for_top_run(
    target: str,
) -> tuple[Optional[Path], Optional[str]]:
    """Phase 6B-2: materialize a stack day-by-day artifact for the
    top (highest-K-or-first) leaderboard row of the studied ticker's
    most recent saved StackBuilder run.

    Returns ``(path, None)`` on success or ``(None, reason_code)``
    when a step in the pipeline cannot resolve. ``reason_code`` is
    one of the short tags below so the Activity log can render a
    differentiated message:

      - ``"no_run"``: no saved StackBuilder run for this ticker
      - ``"target_cache_missing"``: target Spymaster cache PKL absent
      - ``"no_member_caches"``: no readable member cache PKLs
      - ``"write_failed"``: artifact built but write to disk failed
      - ``"engine_unavailable"``: research_artifacts import failed

    Strictly local; no network. Never imports spymaster / trafficflow.
    """
    try:
        import research_artifacts as ra
    except Exception:
        return None, "engine_unavailable"
    if not target or not isinstance(target, str):
        return None, "no_run"
    safe = target.strip().upper()
    runs = _discover_stack_runs(_stack_output_dir())
    target_runs = [r for r in runs if r["ticker"].upper() == safe]
    if not target_runs:
        return None, "no_run"
    run = target_runs[0]
    run_id = run.get("run_dir") or run.get("run_name")
    if not run_id:
        return None, "no_run"
    lb = _load_stack_leaderboard(run["run_path"])
    if lb is None or lb.empty:
        return None, "no_run"
    if "Members" not in lb.columns or "K" not in lb.columns:
        return None, "no_run"
    members_str = str(lb["Members"].iloc[0])
    try:
        K = int(pd.to_numeric(lb["K"], errors="coerce").iloc[0])
    except Exception:
        return None, "no_run"

    # Differentiate the next two failure modes (target cache vs
    # no readable member caches) by probing them ahead of the build.
    cache_base = _spymaster_cache_dir()
    safe_target_file = (
        cache_base
        / f"{ra._normalize_ticker_for_filename(target)}_precomputed_results.pkl"
    )
    if not safe_target_file.exists():
        return None, "target_cache_missing"
    parsed_members = ra.parse_stack_members_with_protocol(members_str)
    any_member_readable = False
    for ticker, _proto in parsed_members:
        m_file = (
            cache_base
            / f"{ra._normalize_ticker_for_filename(ticker)}_precomputed_results.pkl"
        )
        if m_file.exists() and m_file.is_file():
            any_member_readable = True
            break
    if not any_member_readable:
        return None, "no_member_caches"

    overrides = {
        "total_capture_pct": (
            lb["Total Capture (%)"].iloc[0]
            if "Total Capture (%)" in lb.columns else None
        ),
        "sharpe_ratio": (
            lb["Sharpe Ratio"].iloc[0]
            if "Sharpe Ratio" in lb.columns else None
        ),
        "trigger_days": (
            lb["Trigger Days"].iloc[0]
            if "Trigger Days" in lb.columns else None
        ),
        "p_value": (
            lb["p-Value"].iloc[0]
            if "p-Value" in lb.columns else None
        ),
        "significant_95": (
            (
                str(lb["Significant 95%"].iloc[0]).strip().upper()
                == "YES"
            )
            if "Significant 95%" in lb.columns else None
        ),
    }
    artifact = ra.build_stackbuilder_day_artifact_from_local(
        target, run_id, members_str=members_str, K=K,
        summary_overrides=overrides,
    )
    if artifact is None:
        # The pre-flight checks already separated target_cache_missing
        # / no_member_caches paths, so this branch covers the
        # remaining "all members unreadable despite a cache file"
        # case (corrupt PKL etc.).
        return None, "no_member_caches"
    path = ra.artifact_path_for_stackbuilder(target, run_id, K)
    if path is None:
        return None, "write_failed"
    try:
        return ra.write_research_day_artifact(artifact, path), None
    except Exception:
        return None, "write_failed"


def _reason_text(reason: Optional[str], target: str) -> str:
    """Phase 6C-1: short, plain-language sentence for the build-
    missing-charts log lines. Mirrors the reason codes returned by
    the per-engine build helpers so the user sees what saved data is
    missing rather than a vague 'failed' message."""
    target = str(target or "").strip().upper() or "this ticker"
    if reason == "no_run":
        return (
            f"no saved combined-signal study for {target}"
        )
    if reason == "no_libraries":
        return (
            f"no saved time-window data for {target}"
        )
    if reason == "target_cache_missing":
        return (
            f"{target} price cache missing on this computer"
        )
    if reason == "no_member_caches":
        return (
            f"stack member caches are missing for {target}"
        )
    if reason == "build_failed":
        return f"build failed for {target}"
    if reason == "write_failed":
        return (
            f"chart data built for {target} but the file could not "
            "be saved"
        )
    if reason == "engine_unavailable":
        return (
            "research engine unavailable; restart the launcher"
        )
    return f"unknown failure for {target}"


def _spymaster_cache_dir() -> Path:
    """Resolve the standalone Spymaster cache directory the artifact
    builders read. Uses a project-relative path so it works under any
    launcher cwd."""
    return Path(__file__).resolve().parent / "cache" / "results"


def _read_confluence_artifact_for_target(target: str):
    """Phase 6B-3: read the saved confluence day-by-day artifact for
    ``target`` when present. Returns None for missing / corrupt
    files."""
    try:
        import research_artifacts as ra
    except Exception:
        return None
    path = ra.artifact_path_for_confluence(target)
    if path is None:
        return None
    return ra.read_research_day_artifact(path)


def _build_confluence_artifact_for_target(
    target: str,
) -> tuple[Optional[Path], Optional[str]]:
    """Phase 6B-3: materialize a confluence day-by-day artifact for
    the studied ticker.

    Returns ``(path, None)`` on success or ``(None, reason_code)``
    when a step in the pipeline cannot resolve. ``reason_code`` is
    one of:

      - ``"no_libraries"``       : no saved confluence libraries for
                                   the target (no per-timeframe PKL
                                   and no Spymaster fallback cache).
      - ``"target_cache_missing"`` : target Spymaster cache PKL absent
                                     (needed for the daily Close
                                     series).
      - ``"build_failed"``       : the confluence engine ran but
                                   produced no aligned grid.
      - ``"write_failed"``       : artifact built but write failed.
      - ``"engine_unavailable"`` : ``research_artifacts`` import
                                   failed.

    Strictly local; no network. Never imports
    ``confluence.py`` / ``trafficflow.py``.
    """
    try:
        import research_artifacts as ra
    except Exception:
        return None, "engine_unavailable"
    if not target or not isinstance(target, str):
        return None, "no_libraries"
    safe = target.strip().upper()
    if not safe:
        return None, "no_libraries"

    cache_base = _spymaster_cache_dir()
    sig_dir = _signal_library_dir()

    # Resolve target cache path and signal-library form INDEPENDENTLY.
    # Real local files often retain the original ticker form (e.g.,
    # ``^GSPC_precomputed_results.pkl``); the filename-safe form
    # (``_GSPC``) is the artifact-output form. The two file kinds
    # may live under different ticker forms (mixed-form fixtures),
    # so the preflight resolves each side independently.
    target_pkl, _cache_form = ra._resolve_local_target_cache_path(
        target, cache_base,
    )
    if target_pkl is None:
        return None, "target_cache_missing"

    library_form = ra._resolve_local_signal_library_form(
        target, sig_dir, ra.CONFLUENCE_TIMEFRAMES_DEFAULT,
    )
    # The analyzer's 1d Spymaster-cache fallback can satisfy "any
    # library" when no stable PKL exists, using the resolved cache
    # file. So having a cache without libraries does NOT trigger
    # no_libraries; only the all-empty case does.
    if library_form is None and target_pkl is None:
        return None, "no_libraries"

    try:
        artifact = ra.build_confluence_day_artifact_from_local(
            target,
            sig_lib_dir=sig_dir,
            cache_dir=cache_base,
        )
    except Exception:
        return None, "build_failed"
    if artifact is None:
        return None, "build_failed"
    path = ra.artifact_path_for_confluence(target)
    if path is None:
        return None, "write_failed"
    try:
        return ra.write_research_day_artifact(artifact, path), None
    except Exception:
        return None, "write_failed"


def _read_trafficflow_artifact_for_run(target: str, run_id: str):
    """Phase 6B-4: read the saved TrafficFlow day-by-day artifact for
    (``target``, ``run_id``) when present. Returns None for missing /
    corrupt files."""
    try:
        import research_artifacts as ra
    except Exception:
        return None
    path = ra.artifact_path_for_trafficflow(target, run_id)
    if path is None:
        return None
    return ra.read_research_day_artifact(path)


def _build_trafficflow_artifact_for_top_run(
    target: str,
) -> tuple[Optional[Path], Optional[str]]:
    """Phase 6B-4: materialize a TrafficFlow day-by-day pressure
    artifact for the top leaderboard row of the studied ticker's
    most recent saved StackBuilder run.

    Returns ``(path, None)`` on success or ``(None, reason_code)``
    when a step in the pipeline cannot resolve. ``reason_code`` is
    one of:

      - ``"no_run"``            : no saved StackBuilder run for the
                                  target (TrafficFlow needs the
                                  run's member list to drive the
                                  per-day pressure rebuild).
      - ``"target_cache_missing"`` : target Spymaster cache PKL absent
                                     (needed for the daily Close
                                     series).
      - ``"no_member_caches"``  : no readable member cache PKLs.
      - ``"write_failed"``      : artifact built but write to disk
                                  failed.
      - ``"engine_unavailable"``: ``research_artifacts`` import
                                  failed.

    Strictly local; no network. Never imports trafficflow / spymaster.
    """
    try:
        import research_artifacts as ra
    except Exception:
        return None, "engine_unavailable"
    if not target or not isinstance(target, str):
        return None, "no_run"
    safe = target.strip().upper()
    runs = _discover_stack_runs(_stack_output_dir())
    target_runs = [r for r in runs if r["ticker"].upper() == safe]
    if not target_runs:
        return None, "no_run"
    run = target_runs[0]
    run_id = run.get("run_dir") or run.get("run_name")
    if not run_id:
        return None, "no_run"
    lb = _load_stack_leaderboard(run["run_path"])
    if lb is None or lb.empty:
        return None, "no_run"
    if "Members" not in lb.columns:
        return None, "no_run"
    members_str = str(lb["Members"].iloc[0])
    K_val: Optional[int] = None
    if "K" in lb.columns:
        try:
            K_val = int(pd.to_numeric(lb["K"], errors="coerce").iloc[0])
        except Exception:
            K_val = None

    cache_base = _spymaster_cache_dir()
    # Phase 6B-4 Amendment: real-form-first cache resolution. Real
    # local files retain the original symbol (^GSPC); the filename-
    # safe form (_GSPC) is the artifact-output form. The preflight
    # was previously only probing the filename-safe form, which
    # produced spurious target_cache_missing / no_member_caches
    # codes for caret-style indices.
    target_pkl, _target_form = ra._resolve_local_target_cache_path(
        target, cache_base,
    )
    if target_pkl is None:
        return None, "target_cache_missing"
    parsed_members = ra.parse_stack_members_with_protocol(members_str)
    any_member_readable = False
    for ticker, _proto in parsed_members:
        m_path, _m_form = ra._resolve_local_target_cache_path(
            ticker, cache_base,
        )
        if m_path is not None and m_path.is_file():
            any_member_readable = True
            break
    if not any_member_readable:
        return None, "no_member_caches"

    overrides = {
        "total_capture_pct": (
            lb["Total Capture (%)"].iloc[0]
            if "Total Capture (%)" in lb.columns else None
        ),
        "sharpe_ratio": (
            lb["Sharpe Ratio"].iloc[0]
            if "Sharpe Ratio" in lb.columns else None
        ),
        "trigger_days": (
            lb["Trigger Days"].iloc[0]
            if "Trigger Days" in lb.columns else None
        ),
        "p_value": (
            lb["p-Value"].iloc[0]
            if "p-Value" in lb.columns else None
        ),
        "significant_95": (
            (
                str(lb["Significant 95%"].iloc[0]).strip().upper()
                == "YES"
            )
            if "Significant 95%" in lb.columns else None
        ),
    }
    artifact = ra.build_trafficflow_day_artifact_from_local(
        target, run_id,
        members_str=members_str,
        K=K_val,
        cache_dir=cache_base,
        summary_overrides=overrides,
    )
    if artifact is None:
        return None, "no_member_caches"
    path = ra.artifact_path_for_trafficflow(target, run_id)
    if path is None:
        return None, "write_failed"
    try:
        return ra.write_research_day_artifact(artifact, path), None
    except Exception:
        return None, "write_failed"


# Confluence stack: per-timeframe stable library suffixes. Mirrors
# the order used by ``_timeframe_coverage_for_ticker``.
_CONFLUENCE_TIMEFRAMES: list[tuple[str, str]] = [
    ("Daily", ""),
    ("Weekly", "_1wk"),
    ("Monthly", "_1mo"),
    ("Quarterly", "_3mo"),
    ("Yearly", "_1y"),
]


def _confluence_status_for_target(
    target: str,
    sig_lib_dir: Optional[str] = None,
) -> list[dict]:
    """Read each saved timeframe library for ``target`` and report
    the latest signal + how many bars the signal has been in effect.

    Returns a list of dicts with keys ``timeframe``, ``available``,
    ``signal`` (Buy / Short / None / "-"), ``bars_in_signal``,
    ``signal_start_date``. Empty list iff target is invalid.
    Missing libraries render as ``available=False`` with placeholder
    fields so the cockpit can render an honest "missing" cell.
    """
    if not target or not isinstance(target, str):
        return []
    rows: list[dict] = []
    for label, suffix in _CONFLUENCE_TIMEFRAMES:
        lib = _load_stable_signal_library(target, sig_lib_dir, suffix)
        if lib is None:
            rows.append({
                "timeframe": label,
                "available": False,
                "signal": "-",
                "bars_in_signal": None,
                "signal_start_date": None,
            })
            continue
        sigs = lib.get("primary_signals")
        dates = lib.get("dates")
        if not sigs or not dates:
            rows.append({
                "timeframe": label,
                "available": True,
                "signal": "-",
                "bars_in_signal": None,
                "signal_start_date": None,
            })
            continue
        try:
            sigs_list = [str(s).strip() for s in sigs]
            dates_list = list(dates)
        except Exception:
            rows.append({
                "timeframe": label,
                "available": True,
                "signal": "-",
                "bars_in_signal": None,
                "signal_start_date": None,
            })
            continue
        if not sigs_list:
            rows.append({
                "timeframe": label,
                "available": True,
                "signal": "-",
                "bars_in_signal": None,
                "signal_start_date": None,
            })
            continue
        last = sigs_list[-1] or "None"
        # Walk backward to count consecutive bars with the same signal.
        run = 1
        for i in range(len(sigs_list) - 2, -1, -1):
            if sigs_list[i] == last:
                run += 1
            else:
                break
        start_idx = max(0, len(sigs_list) - run)
        try:
            start_date = pd.to_datetime(dates_list[start_idx])
            start_str = (
                start_date.strftime("%Y-%m-%d")
                if pd.notna(start_date) else None
            )
        except Exception:
            start_str = None
        rows.append({
            "timeframe": label,
            "available": True,
            "signal": last if last in ("Buy", "Short", "None") else last,
            "bars_in_signal": int(run),
            "signal_start_date": start_str,
        })
    return rows


def _real_confluence_snapshot_for_target(
    target: str,
    sig_lib_dir: Optional[str] = None,
) -> Optional[dict]:
    """Run the real ``signal_library.confluence_analyzer`` engine on
    saved local timeframe libraries and return a snapshot dict.

    Strictly read-only / offline. Calls ``load_confluence_data``,
    ``align_signals_to_daily``, ``calculate_confluence``, and
    ``calculate_time_in_signal``. The engine reads the same
    ``signal_library/data/stable/*.pkl`` files the preview already
    uses; no yfinance.

    Returns:
        ``{
            'tier': 'Strong Buy' | 'Buy' | 'Weak Buy' | 'Neutral' |
                    'Weak Short' | 'Short' | 'Strong Short',
            'strength': 'STRONG' | 'MODERATE' | 'WEAK' | 'MIXED',
            'alignment_pct': float (0.0 - 100.0),
            'buy_count': int, 'short_count': int, 'none_count': int,
            'active_count': int, 'total_count': int,
            'alignment_since': iso-string or None,
            'breakdown': {'1d': 'Buy', ...},
            'time_in_signal': {
                '1d': {
                    'signal': 'Buy', 'entry_date_iso': '2026-01-21',
                    'days': int, 'bars': int,
                },
                ...
            },
            'as_of': iso-string of the date the snapshot was taken
        }``

    Returns ``None`` if the confluence engine cannot be imported, no
    saved libraries exist for the target, or alignment yields an
    empty grid.
    """
    if not target or not isinstance(target, str):
        return None
    safe = target.strip().upper()
    if not safe:
        return None
    project_dir = Path(__file__).resolve().parent
    saved_cwd = os.getcwd()
    try:
        os.chdir(project_dir)
        try:
            from signal_library.confluence_analyzer import (
                load_confluence_data,
                align_signals_to_daily,
                calculate_confluence,
                calculate_time_in_signal,
            )
        except Exception:
            return None
        try:
            libs = load_confluence_data(safe)
        except Exception:
            return None
        if not libs:
            return None
        try:
            aligned = align_signals_to_daily(libs)
        except Exception:
            return None
        if aligned is None or aligned.empty:
            return None
        as_of = aligned.index[-1]
        try:
            conf = calculate_confluence(aligned, as_of)
        except Exception:
            return None
        if not isinstance(conf, dict):
            return None
        try:
            tis = calculate_time_in_signal(libs, as_of)
        except Exception:
            tis = {}
        norm_tis = {}
        for tf, info in (tis or {}).items():
            if not isinstance(info, dict):
                continue
            entry_iso = info.get("entry_date_iso")
            if entry_iso is None:
                ed = info.get("entry_date")
                if ed is not None:
                    try:
                        entry_iso = pd.Timestamp(ed).strftime("%Y-%m-%d")
                    except Exception:
                        entry_iso = None
            norm_tis[str(tf)] = {
                "signal": str(info.get("signal") or "-"),
                "entry_date_iso": entry_iso,
                "days": int(info.get("days") or 0),
                "bars": int(info.get("bars") or 0),
            }
        return {
            "tier": str(conf.get("tier") or "Neutral"),
            "strength": str(conf.get("strength") or ""),
            "alignment_pct": float(conf.get("alignment_pct") or 0.0),
            "buy_count": int(conf.get("buy_count") or 0),
            "short_count": int(conf.get("short_count") or 0),
            "none_count": int(conf.get("none_count") or 0),
            "active_count": int(conf.get("active_count") or 0),
            "total_count": int(conf.get("total_count") or 0),
            "alignment_since": conf.get("alignment_since"),
            "breakdown": dict(conf.get("breakdown") or {}),
            "time_in_signal": norm_tis,
            "as_of": (
                pd.Timestamp(as_of).strftime("%Y-%m-%d")
                if as_of is not None else None
            ),
        }
    finally:
        try:
            os.chdir(saved_cwd)
        except Exception:
            pass


def _traffic_flow_snapshot_for_target(
    target: str,
    stack_root: Optional[str] = None,
) -> Optional[dict]:
    """Read-only Traffic Flow snapshot for the studied ticker.

    Reads the most recent saved StackBuilder run for ``target`` and
    parses its ``combo_leaderboard`` for the top stack's members,
    then queries each member's ``cache/results/<MEMBER>_precomputed
    _results.pkl`` for the latest ``Buy`` / ``Short`` / ``None``
    signal via ``trafficflow._next_signal_from_pkl``. Builds a Buy /
    Short / None count summary plus a direct/inverse protocol mix.

    Strictly read-only. Calls ONLY parse_members_with_protocol +
    _next_signal_from_pkl + _calculate_signal_mix from trafficflow.
    Does NOT call ``_signal_snapshot_for_members`` (which would
    invoke yfinance if the secondary price cache is missing).

    Returns ``None`` if no saved stack run exists for ``target`` or
    the leaderboard cannot be read.

    Returns:
        ``{
            'target': 'SPY',
            'run_path': str,
            'top_k': int,
            'members': [
                {'ticker': 'XLF', 'protocol': 'D' | 'I' | None,
                 'signal': 'Buy' | 'Short' | 'None' | 'missing'},
                ...
            ],
            'buy_count': int, 'short_count': int,
            'none_count': int, 'missing_count': int,
            'pressure': 'Buy pressure' | 'Short pressure' |
                        'Mixed' | 'None',
            'protocol_mix': '2/3' (count of members whose signal
                            agrees with their direct/inverse marker)
        }``
    """
    if not target or not isinstance(target, str):
        return None
    safe = target.strip().upper()
    if not safe:
        return None
    runs = _discover_stack_runs(_stack_output_dir() if stack_root is None
                                else Path(stack_root))
    target_runs = [r for r in runs if r["ticker"].upper() == safe]
    if not target_runs:
        return None
    run = target_runs[0]
    lb = _load_stack_leaderboard(run["run_path"])
    if lb is None or lb.empty or "Members" not in lb.columns:
        return None
    members_str = str(lb["Members"].iloc[0])
    try:
        K = int(lb["K"].iloc[0])
    except Exception:
        K = None

    project_dir = Path(__file__).resolve().parent
    saved_cwd = os.getcwd()
    try:
        try:
            os.chdir(project_dir)
        except Exception:
            pass
        try:
            import trafficflow as _tf
        except Exception:
            return None
        try:
            members_with_protocol = (
                _tf.parse_members_with_protocol(members_str)
            )
        except Exception:
            members_with_protocol = []
        member_rows = []
        buy_n = short_n = none_n = missing_n = 0
        for ticker, protocol in members_with_protocol:
            try:
                pkl = _tf.load_spymaster_pkl(ticker)
            except Exception:
                pkl = None
            if pkl is None:
                signal = "missing"
                missing_n += 1
            else:
                try:
                    signal = _tf._next_signal_from_pkl(ticker)
                except Exception:
                    signal = "missing"
                if signal == "Buy":
                    buy_n += 1
                elif signal == "Short":
                    short_n += 1
                elif signal == "None":
                    none_n += 1
                else:
                    missing_n += 1
                    signal = "missing"
            member_rows.append({
                "ticker": str(ticker).upper(),
                "protocol": protocol,
                "signal": signal,
            })
        try:
            protocol_mix = _tf._calculate_signal_mix(
                members_with_protocol,
            )
        except Exception:
            protocol_mix = None

        if buy_n > 0 and short_n == 0:
            pressure = "Buy pressure"
        elif short_n > 0 and buy_n == 0:
            pressure = "Short pressure"
        elif buy_n > 0 and short_n > 0:
            pressure = "Mixed"
        else:
            pressure = "None"

        return {
            "target": safe,
            "run_path": str(run["run_path"]),
            "top_k": K,
            "members": member_rows,
            "buy_count": buy_n,
            "short_count": short_n,
            "none_count": none_n,
            "missing_count": missing_n,
            "pressure": pressure,
            "protocol_mix": protocol_mix,
        }
    finally:
        try:
            os.chdir(saved_cwd)
        except Exception:
            pass


def _selected_row_from_table_state(
    virtual_data: Optional[list], selected_rows: Optional[list]
) -> Optional[dict]:
    """Resolve the user's selected row from a DataTable's derived state.

    ``virtual_data`` is the post-sort/post-filter row list (the
    ``derived_virtual_data`` property of ``dash_table.DataTable``);
    ``selected_rows`` is the list of indices into ``virtual_data``
    (the ``derived_virtual_selected_rows`` property). Returns the
    selected row dict, or None when no selection / out-of-range.
    """
    if not selected_rows:
        return None
    if not virtual_data:
        return None
    try:
        idx = int(selected_rows[0])
    except (TypeError, ValueError):
        return None
    if idx < 0 or idx >= len(virtual_data):
        return None
    row = virtual_data[idx]
    if not isinstance(row, dict):
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# Live engine preload
# ---------------------------------------------------------------------------
#
# Importing ``impactsearch`` pulls Dash component libraries (the engine's UI
# imports Dash for its operator dashboards), and Dash refuses any
# component-library import that happens during a callback execution
# (``ImportedInsideCallbackError``). Pattern:
#
#   1. ``main()`` calls ``preload_live_engine()`` BEFORE ``build_app()`` and
#      ``app.run(...)``.
#   2. The cached engine module sits in ``_IMPACTSEARCH_ENGINE``. Any import
#      error sits in ``_IMPACTSEARCH_IMPORT_ERROR``.
#   3. ``_run_live_preview(...)`` reads the cached module instead of
#      importing inside the callback.
#
# Tests can either monkeypatch ``_IMPACTSEARCH_ENGINE`` directly or call
# ``preload_live_engine()`` against a synthesized ``sys.modules['impactsearch']``.

_IMPACTSEARCH_ENGINE: Any = None
_IMPACTSEARCH_IMPORT_ERROR: Optional[str] = None


def preload_live_engine() -> bool:
    """Import ``impactsearch`` once at startup, BEFORE Dash callbacks run.

    Stores the imported module in ``_IMPACTSEARCH_ENGINE`` on success
    (or any prior cached value if already loaded). On failure stores
    a human-readable message in ``_IMPACTSEARCH_IMPORT_ERROR``.
    Returns True iff the engine is available after the call.
    """
    global _IMPACTSEARCH_ENGINE, _IMPACTSEARCH_IMPORT_ERROR
    if _IMPACTSEARCH_ENGINE is not None:
        return True
    try:
        import impactsearch as _impact  # noqa: F401 — heavy module
    except Exception as exc:
        _IMPACTSEARCH_IMPORT_ERROR = (
            f"failed to import impactsearch: {type(exc).__name__}: {exc}"
        )
        return False
    _IMPACTSEARCH_ENGINE = _impact
    _IMPACTSEARCH_IMPORT_ERROR = None
    return True


def _live_engine_status() -> dict:
    """Snapshot of the preloaded-engine state for the Run Log."""
    return {
        "preloaded": _IMPACTSEARCH_ENGINE is not None,
        "import_error": _IMPACTSEARCH_IMPORT_ERROR,
    }


def _run_live_preview(
    target: str, primaries: list[str]
) -> dict:
    """Bounded live ImpactSearch preview wrapper.

    Reads the preloaded engine from ``_IMPACTSEARCH_ENGINE`` (set by
    ``preload_live_engine`` at startup). DOES NOT import
    ``impactsearch`` inside this function — that would trigger Dash's
    ImportedInsideCallbackError because the engine pulls Dash
    component libraries. Calls
    ``impactsearch.process_primary_tickers(target, primaries,
    use_multiprocessing=False, mark_complete=True)`` with the hard
    limits enforced before invocation. Returns a dict:

        {
            "ok": bool,
            "rows": list[dict] | None,
            "rejection": dict | None,
            "error": str | None,
            "elapsed_seconds": float | None,
        }

    Never raises. The caller is expected to surface ``error`` /
    ``rejection`` through the Run Log and to convert ``rows`` into a
    DataFrame via ``_normalize_results_frame``.
    """
    target_clean = (target or "").strip().upper()
    if not target_clean:
        return {
            "ok": False,
            "rows": None,
            "rejection": None,
            "error": "no target ticker supplied",
            "elapsed_seconds": None,
        }
    primaries_clean = [p.strip().upper() for p in (primaries or []) if p and p.strip()]
    if not primaries_clean:
        return {
            "ok": False,
            "rows": None,
            "rejection": None,
            "error": "no primary tickers supplied",
            "elapsed_seconds": None,
        }
    if len(primaries_clean) > MAX_PRIMARIES_LIVE:
        return {
            "ok": False,
            "rows": None,
            "rejection": None,
            "error": (
                f"{len(primaries_clean)} primaries exceeds "
                f"MAX_PRIMARIES_LIVE={MAX_PRIMARIES_LIVE}"
            ),
            "elapsed_seconds": None,
        }
    engine = _IMPACTSEARCH_ENGINE
    if engine is None:
        detail = (
            f" ({_IMPACTSEARCH_IMPORT_ERROR})"
            if _IMPACTSEARCH_IMPORT_ERROR else ""
        )
        return {
            "ok": False,
            "rows": None,
            "rejection": None,
            "error": (
                "ImpactSearch live engine was not preloaded; restart "
                f"with the Phase 6 launcher.{detail}"
            ),
            "elapsed_seconds": None,
        }

    started = datetime.now(timezone.utc)
    rejection: dict = {}
    # ImpactSearch + signal_library/global_ticker_library expect a
    # project-relative cwd (they read e.g. ``global_ticker_library/
    # data/master_tickers.txt`` and ``signal_library/data/...``). When
    # the Phase 6 preview is launched from the repo root the relative
    # paths miss, which previously printed
    #   "Could not load master tickers from
    #    global_ticker_library/data/master_tickers.txt"
    # to the console. Defensively switch cwd to the project dir for
    # the duration of the engine call and restore it on exit.
    project_dir = Path(__file__).resolve().parent
    saved_cwd = os.getcwd()
    try:
        try:
            os.chdir(project_dir)
        except OSError:
            pass
        rows = engine.process_primary_tickers(
            target_clean,
            primaries_clean,
            use_multiprocessing=False,
            mark_complete=True,
            rejection_out=rejection,
        )
    except Exception as exc:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        os.chdir(saved_cwd)
        return {
            "ok": False,
            "rows": None,
            "rejection": rejection or None,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": elapsed,
        }
    finally:
        os.chdir(saved_cwd)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    if rows is None:
        rows = []
    return {
        "ok": True,
        "rows": list(rows),
        "rejection": rejection or None,
        "error": None,
        "elapsed_seconds": elapsed,
    }


def _sidecar_path_for(xlsx_path: Path) -> Path:
    """Sidecar manifest path adjacent to the XLSX (if it exists)."""
    return xlsx_path.with_suffix(xlsx_path.suffix + ".manifest.json")


def _read_sidecar(xlsx_path: Path) -> Optional[dict]:
    """Read a sidecar JSON manifest if present; tolerant of all errors."""
    sidecar = _sidecar_path_for(xlsx_path)
    if not sidecar.exists():
        return None
    try:
        with open(sidecar, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _resolve_xlsx_for_target(target: str, output_dir: Path) -> Optional[Path]:
    """Find the best XLSX path for a target ticker under ``output_dir``."""
    if not target:
        return None
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return None
    for name in _candidate_xlsx_filenames_for(target):
        candidate = output_dir / name
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Dash app builder
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """Return the project root directory (the dir containing this file)."""
    return Path(__file__).resolve().parent


def _output_dir() -> Path:
    return _project_root() / OUTPUT_SUBDIR


def _format_summary_card(label: str, value: str) -> dict:
    return {"label": label, "value": value}


def _summary_cards(summary: dict) -> list[dict]:
    rows = summary.get("rows", 0)
    best = summary.get("best_total_capture")
    best_primary = summary.get("best_total_capture_primary")
    median_sharpe = summary.get("median_sharpe")
    t_min = summary.get("trigger_days_min")
    t_max = summary.get("trigger_days_max")

    def _fmt_num(v, fmt=".2f"):
        if v is None:
            return "-"
        try:
            return format(float(v), fmt)
        except Exception:
            return str(v)

    cards = [
        _format_summary_card("Rows", str(rows)),
        _format_summary_card(
            "Best Total Capture (%)",
            f"{_fmt_num(best)}{'  (' + best_primary + ')' if best_primary else ''}",
        ),
        _format_summary_card("Median Sharpe", _fmt_num(median_sharpe)),
        _format_summary_card(
            "Trigger Day Range",
            f"{t_min if t_min is not None else '-'} -> {t_max if t_max is not None else '-'}",
        ),
    ]
    return cards


# ---------------------------------------------------------------------------
# Research-mode discovery: StackBuilder + Timeframes + Signal Engine
# ---------------------------------------------------------------------------

# Multi-timeframe signal libraries land under ``signal_library/data/stable/``
# as ``<TICKER>_stable_v1_0_0.pkl`` (daily) plus per-interval suffixes
# ``_1wk.pkl`` / ``_1mo.pkl`` / ``_3mo.pkl`` / ``_1y.pkl``. The Phase 6A
# preview's Timeframes tab does NOT load these libraries — it only checks
# whether the file exists, so heavy compute and module imports are never
# triggered by Dash callbacks.
SIGNAL_LIBRARY_STABLE_SUBDIR = Path("signal_library") / "data" / "stable"
TIMEFRAME_LABELS: list[tuple[str, str]] = [
    ("Daily", ""),
    ("Weekly", "_1wk"),
    ("Monthly", "_1mo"),
    ("Quarterly", "_3mo"),
    ("Yearly", "_1y"),
]
SIGNAL_LIB_VERSION_SUFFIX = "_stable_v1_0_0"

STACK_OUTPUT_SUBDIR = Path("output") / "stackbuilder"

# Default MAX_SMA_DAY for the Signal Engine info panel. Read directly
# from impactsearch when the engine has been preloaded; otherwise fall
# back to the documented value (impactsearch.py:704). UI-only.
DEFAULT_MAX_SMA_DAY = 114


def _stack_output_dir() -> Path:
    return _project_root() / STACK_OUTPUT_SUBDIR


def _signal_library_dir() -> Path:
    return _project_root() / SIGNAL_LIBRARY_STABLE_SUBDIR


def _discover_stack_runs(stack_root: Path) -> list[dict]:
    """Walk ``output/stackbuilder/<TICKER>/<run_dir>/`` and return one
    dict per run found. Tolerates a missing root.

    Each returned dict contains:
      ``ticker``     : the studied ticker (parent dir name)
      ``run_name``   : the run-dir basename
      ``run_path``   : absolute Path to the run directory
      ``has_summary``      : True iff ``summary.json`` exists
      ``has_leaderboard``  : True iff a combo_leaderboard.{xlsx,csv,parquet}
                            exists
    """
    runs: list[dict] = []
    if stack_root is None:
        return runs
    root = Path(stack_root)
    if not root.exists() or not root.is_dir():
        return runs
    for ticker_dir in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not ticker_dir.is_dir():
            continue
        for run_dir in sorted(ticker_dir.iterdir(), key=lambda p: p.name.lower()):
            if not run_dir.is_dir():
                continue
            if run_dir.name.startswith("_") or run_dir.name.startswith("."):
                continue
            summary_path = run_dir / "summary.json"
            has_lb = any(
                (run_dir / f"combo_leaderboard{ext}").exists()
                for ext in (".xlsx", ".csv", ".parquet")
            )
            runs.append({
                "ticker": ticker_dir.name,
                "run_name": run_dir.name,
                "run_path": run_dir,
                "has_summary": summary_path.exists(),
                "has_leaderboard": has_lb,
            })
    return runs


def _load_stack_summary(run_path: Path) -> Optional[dict]:
    """Read a stack run's ``summary.json`` if it exists. Returns None on
    missing / unreadable / non-JSON file. Never raises."""
    if run_path is None:
        return None
    p = Path(run_path) / "summary.json"
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _load_stack_leaderboard(run_path: Path) -> pd.DataFrame:
    """Read combo_leaderboard.{xlsx,csv,parquet} (in that preference
    order) from a stack run directory. Returns an empty DataFrame on
    missing / unreadable file, never raises.

    The expected schema is ``K, Trigger Days, Total Capture (%),
    Sharpe Ratio, p-Value, Members``; missing columns are tolerated.
    """
    if run_path is None:
        return pd.DataFrame()
    base = Path(run_path) / "combo_leaderboard"
    for ext, reader in [
        (".xlsx", lambda p: pd.read_excel(p, engine="openpyxl")),
        (".csv", lambda p: pd.read_csv(p)),
        (".parquet", lambda p: pd.read_parquet(p)),
    ]:
        candidate = Path(str(base) + ext)
        if not candidate.exists():
            continue
        try:
            return reader(candidate)
        except Exception:
            continue
    return pd.DataFrame()


def _stack_run_card(run: dict, summary: Optional[dict]) -> dict:
    """Compact, plain-language summary card payload for one stack run.
    Pure data; the UI renderer turns it into Dash components.
    """
    s = summary or {}
    return {
        "ticker_studied": run.get("ticker", ""),
        "run_name": run.get("run_name", ""),
        "final_stack_size": s.get("final_stack_size"),
        "best_risk_adjusted_score": s.get("best_sharpe"),
        "best_total_move": s.get("best_capture"),
        "signal_days_at_best": s.get("best_trigger_days"),
        "primaries_tested": s.get("primaries_tested"),
        "elapsed_label": s.get("elapsed_formatted"),
    }


def _timeframe_coverage_for_ticker(
    ticker: str, signal_lib_dir: Path,
) -> list[dict]:
    """Return one row per timeframe describing whether a signal-library
    file exists for ``ticker``. Filesystem-only — does not load PKLs.

    Each row has keys ``label`` (Daily/Weekly/...), ``interval``
    (the suffix shorthand 1d/1wk/...), ``available`` (bool), and
    ``filename`` (the resolved name when available, "" otherwise).

    Daily uses the unsuffixed file; other intervals use the suffix
    convention ``<TICKER>_stable_v1_0_0_<suffix>.pkl``.
    """
    rows: list[dict] = []
    cleaned = (ticker or "").strip()
    if not cleaned:
        return rows
    p = Path(signal_lib_dir)
    interval_for_label = {
        "Daily": "1d",
        "Weekly": "1wk",
        "Monthly": "1mo",
        "Quarterly": "3mo",
        "Yearly": "1y",
    }
    for label, suffix in TIMEFRAME_LABELS:
        filename = f"{cleaned}{SIGNAL_LIB_VERSION_SUFFIX}{suffix}.pkl"
        candidate = p / filename
        rows.append({
            "label": label,
            "interval": interval_for_label[label],
            "available": candidate.exists(),
            "filename": filename if candidate.exists() else "",
        })
    return rows


def _signal_engine_settings() -> dict:
    """Return a tiny dict describing the signal engine in plain terms.

    Pulls ``MAX_SMA_DAY`` from the preloaded impactsearch engine when
    available; otherwise reports the documented constant. UI-only —
    never used in scoring math.
    """
    max_sma_day = DEFAULT_MAX_SMA_DAY
    source = "documented value (impactsearch.MAX_SMA_DAY)"
    engine = _IMPACTSEARCH_ENGINE
    if engine is not None:
        v = getattr(engine, "MAX_SMA_DAY", None)
        if isinstance(v, int) and v > 0:
            max_sma_day = v
            source = "live engine"
    return {
        "max_sma_day": int(max_sma_day),
        "source": source,
        "price_basis": "raw Close",
        "single_signal_cadence": "daily close-to-close",
    }


def build_app() -> Any:
    """Construct the Dash app. Imports Dash lazily."""
    import dash
    from dash import Dash, dcc, html, dash_table, callback_context, no_update
    from dash.dependencies import Input, Output, State
    from dash.dash_table.Format import Format, Scheme

    base_style = {
        "backgroundColor": PRJCT9_BLACK,
        "color": PRJCT9_TEXT,
        "fontFamily": "Consolas, 'Courier New', monospace",
        "minHeight": "100vh",
        "margin": "0",
        "padding": "0",
        "boxSizing": "border-box",
    }
    panel_style = {
        "backgroundColor": PRJCT9_DIM,
        "border": f"1px solid {PRJCT9_BORDER}",
        "padding": "12px 14px",
        "borderRadius": "2px",
        "boxSizing": "border-box",
        "maxWidth": "100%",
    }
    pill_style = {
        "border": f"1px solid {PRJCT9_GREEN}",
        "color": PRJCT9_GREEN,
        "padding": "2px 8px",
        "fontSize": "11px",
        "letterSpacing": "1px",
        "textTransform": "uppercase",
        "marginLeft": "12px",
    }
    btn_style = {
        "backgroundColor": PRJCT9_BLACK,
        "color": PRJCT9_GREEN,
        "border": f"1px solid {PRJCT9_GREEN}",
        "padding": "8px 14px",
        "fontFamily": "inherit",
        "cursor": "pointer",
        "marginRight": "8px",
        "marginBottom": "8px",
        "letterSpacing": "1px",
        "textTransform": "uppercase",
        "fontSize": "11px",
        "boxSizing": "border-box",
    }
    label_style = {
        "color": PRJCT9_MUTED,
        "fontSize": "11px",
        "letterSpacing": "1px",
        "textTransform": "uppercase",
        "marginTop": "10px",
        "marginBottom": "4px",
    }
    input_style = {
        "backgroundColor": PRJCT9_BLACK,
        "color": PRJCT9_TEXT,
        "border": f"1px solid {PRJCT9_BORDER}",
        "padding": "6px 8px",
        "width": "100%",
        "fontFamily": "inherit",
        "fontSize": "13px",
        "boxSizing": "border-box",
    }

    app = Dash(
        __name__,
        title="PRJCT9 Research Engine - Local Preview",
        update_title=None,
        suppress_callback_exceptions=True,
    )
    # Inject a tiny responsive stylesheet inline so the preview stays
    # self-contained (no new asset files). At <= 720px the controls
    # stack above the main panel; above that they sit side-by-side.
    app.index_string = """<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        {%favicon%}
        {%css%}
        <style>
            html, body {
                margin: 0; padding: 0; background: #000;
                overflow-x: hidden;
            }
            * { box-sizing: border-box; }
            .prjct9-shell {
                display: flex;
                flex-wrap: wrap;
                gap: 12px;
                padding: 12px;
                align-items: flex-start;
            }
            .prjct9-controls {
                flex: 0 1 280px;
                min-width: 0;
                max-width: 100%;
                overflow-wrap: break-word;
                word-break: break-word;
            }
            .prjct9-controls .Select,
            .prjct9-controls .Select-control,
            .prjct9-controls .Select-input,
            .prjct9-controls input,
            .prjct9-controls textarea {
                max-width: 100%;
            }
            .prjct9-main {
                flex: 1 1 560px;
                min-width: 0;
                overflow-x: hidden;
            }
            .prjct9-charts {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
                gap: 10px;
            }
            .prjct9-cards {
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
            }
            .prjct9-cards > div {
                flex: 1 1 160px;
                min-width: 0;
                overflow-wrap: break-word;
                word-break: break-word;
            }
            .prjct9-cockpit-grid {
                display: grid;
                grid-template-columns: minmax(0, 1fr);
                gap: 12px;
                margin-top: 8px;
            }
            .prjct9-glance-grid {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 8px;
                margin-bottom: 10px;
                max-width: 100%;
                width: 100%;
            }
            .prjct9-firstview-row {
                display: grid;
                grid-template-columns:
                    minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr);
                gap: 10px;
            }
            .prjct9-detail-stack {
                display: grid;
                grid-template-columns: minmax(0, 1fr);
                gap: 12px;
                margin-top: 14px;
                padding-top: 8px;
                border-top: 1px solid #222;
            }
            .prjct9-cockpit-panel {
                border: 1px solid #222;
                background: #0a0a0a;
                padding: 10px 12px;
                min-width: 0;
                overflow: hidden;
                display: flex;
                flex-direction: column;
            }
            .prjct9-cockpit-panel-body {
                flex: 1 1 auto;
                min-width: 0;
            }
            /* First-view summary panels never scroll internally — they
               must read at a glance. Lower-page detail sections scroll
               with the rest of the page. */
            .prjct9-firstview-row > .prjct9-cockpit-panel,
            .prjct9-firstview-row > .prjct9-cockpit-panel
                > .prjct9-cockpit-panel-body {
                overflow: visible;
            }
            @media (max-width: 1100px) {
                .prjct9-firstview-row {
                    grid-template-columns: minmax(0, 1fr);
                }
                .prjct9-glance-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }
            }
            @media (max-width: 720px) {
                .prjct9-shell { padding: 8px; gap: 8px; }
                .prjct9-controls,
                .prjct9-main { flex: 1 1 100%; }
                .prjct9-header-tagline { display: none; }
                .prjct9-charts {
                    grid-template-columns: 1fr;
                }
                /* Mobile uses a tight 2x2 At-a-glance grid so all four
                   cards fit in the first viewport at 390x844 without
                   scrolling. Card descriptions are kept short so they
                   wrap on a single visible line at this column width;
                   overflow stays visible so nothing clips on devices
                   that round padding differently. */
                .prjct9-glance-grid {
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    gap: 6px;
                }
                .prjct9-glance-grid > div {
                    padding: 6px 8px;
                    overflow: visible;
                }
                .prjct9-glance-grid > div > div {
                    word-break: normal;
                    overflow-wrap: break-word;
                    hyphens: none;
                }
                /* Slightly shrink the at-a-glance values + descriptions
                   on mobile so they fit a 2x2 grid at 390px without
                   clipping at the card right edge. */
                .prjct9-glance-grid > div > div:nth-child(2) {
                    font-size: 12px;
                }
                .prjct9-glance-grid > div > div:nth-child(3) {
                    font-size: 10px;
                    line-height: 1.35;
                }
                /* The helper sentence below the cards reads cleanly
                   on desktop; at narrow viewports the long sentence
                   reliably mid-word-clips when the chart panel below
                   pushes the parent width slightly past viewport.
                   Hide it on mobile and let the cards stand alone. */
                .prjct9-glance-helper {
                    display: none;
                }
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""

    app.layout = html.Div(
        style=base_style,
        children=[
            # Header bar
            html.Div(
                style={
                    "display": "flex",
                    "alignItems": "center",
                    "flexWrap": "wrap",
                    "padding": "10px 12px",
                    "borderBottom": f"1px solid {PRJCT9_BORDER}",
                    "gap": "8px",
                },
                children=[
                    html.Span(
                        "PRJCT9 Research Engine",
                        style={
                            "color": PRJCT9_GREEN,
                            "fontWeight": "bold",
                            "letterSpacing": "2px",
                            "fontSize": "15px",
                        },
                    ),
                    html.Span("Local Preview", style=pill_style),
                    html.Span(
                        "Historical research, not investment advice.",
                        className="prjct9-header-tagline",
                        style={
                            "color": PRJCT9_MUTED,
                            "fontSize": "11px",
                            "marginLeft": "auto",
                        },
                    ),
                ],
            ),

            # Responsive shell: controls + main panel
            html.Div(
                className="prjct9-shell",
                children=[
                    # Left control panel: Research flow.
                    # Five compact steps walk the user through OnePass
                    # market scan -> ticker study -> live signal-source
                    # test -> saved combined-signal studies -> saved
                    # time-window coverage. Buttons are explicit.
                    html.Div(
                        className="prjct9-controls",
                        style=panel_style,
                        children=[
                            html.Div(
                                "Start here",
                                style={"color": PRJCT9_GREEN,
                                       "fontWeight": "bold",
                                       "letterSpacing": "2px",
                                       "fontSize": "13px",
                                       "marginBottom": "4px",
                                       "textTransform": "uppercase"},
                            ),
                            html.Div(
                                "Scan first. Then study a ticker.",
                                style={"color": PRJCT9_MUTED,
                                       "fontSize": "11px",
                                       "lineHeight": "1.5",
                                       "marginBottom": "10px"},
                            ),

                            # Step 1: Market scan (OnePass)
                            html.Div(
                                "1. Scan market",
                                style={**label_style,
                                       "color": PRJCT9_GREEN,
                                       "fontWeight": "bold"},
                            ),
                            html.Div(
                                id="market-scan-status",
                                style={"color": PRJCT9_MUTED,
                                       "fontSize": "11px",
                                       "lineHeight": "1.5",
                                       "marginTop": "2px",
                                       "marginBottom": "6px",
                                       "wordBreak": "break-word"},
                            ),
                            html.Button(
                                "Open market scan",
                                id="btn-market-scan",
                                n_clicks=0,
                                style={**btn_style,
                                       "marginRight": "0",
                                       "marginBottom": "0",
                                       "width": "100%",
                                       "boxSizing": "border-box"},
                            ),

                            # Step 2: Ticker study
                            html.Div(
                                "2. Study ticker",
                                style={**label_style,
                                       "color": PRJCT9_GREEN,
                                       "fontWeight": "bold",
                                       "marginTop": "14px"},
                            ),
                            dcc.Input(
                                id="target-ticker",
                                type="text",
                                value=DEFAULT_TARGET,
                                style={**input_style,
                                       "width": "100%",
                                       "boxSizing": "border-box"},
                                debounce=True,
                            ),
                            html.Button(
                                "Open saved ticker study",
                                id="btn-load",
                                n_clicks=0,
                                style={**btn_style,
                                       "backgroundColor": PRJCT9_GREEN,
                                       "color": PRJCT9_BLACK,
                                       "fontWeight": "bold",
                                       "marginRight": "0",
                                       "marginTop": "8px",
                                       "marginBottom": "0",
                                       "width": "100%",
                                       "boxSizing": "border-box"},
                            ),
                            # Phase 6C-1: catalogue refresh + unified
                            # build-missing-charts buttons. Both act on
                            # the currently studied ticker only; they
                            # never trigger a universe-wide rebuild.
                            html.Button(
                                "Build missing charts",
                                id="btn-build-missing-charts",
                                n_clicks=0,
                                style={**btn_style,
                                       "marginRight": "0",
                                       "marginTop": "6px",
                                       "marginBottom": "0",
                                       "width": "100%",
                                       "boxSizing": "border-box"},
                            ),
                            html.Button(
                                "Refresh catalogue",
                                id="btn-refresh-catalogue",
                                n_clicks=0,
                                style={**btn_style,
                                       "marginRight": "0",
                                       "marginTop": "6px",
                                       "marginBottom": "0",
                                       "width": "100%",
                                       "boxSizing": "border-box"},
                            ),
                            # Phase 6C-2: refresh the cross-ticker
                            # catalogue snapshot index from disk and
                            # persist a fresh JSON snapshot for the
                            # next process-restart fast-load.
                            html.Button(
                                "Refresh catalogue index",
                                id="btn-refresh-catalogue-index",
                                n_clicks=0,
                                style={**btn_style,
                                       "marginRight": "0",
                                       "marginTop": "6px",
                                       "marginBottom": "0",
                                       "width": "100%",
                                       "boxSizing": "border-box"},
                            ),
                            html.Div(
                                id="output-discovery-status",
                                style={
                                    "marginTop": "8px",
                                    "fontSize": "11px",
                                    "color": PRJCT9_MUTED,
                                    "lineHeight": "1.5",
                                    "maxWidth": "100%",
                                    "wordBreak": "break-word",
                                    "overflowWrap": "break-word",
                                },
                            ),

                            # Signal sources for the live test
                            # (collapsed by default so the rest of
                            # the research-flow steps stay visible).
                            html.Details(
                                style={"marginTop": "14px",
                                       "borderTop": f"1px dashed {PRJCT9_BORDER}",
                                       "paddingTop": "10px"},
                                children=[
                                    html.Summary(
                                        "Signal sources for live test",
                                        style={"color": PRJCT9_MUTED,
                                               "fontSize": "11px",
                                               "letterSpacing": "1px",
                                               "textTransform": "uppercase",
                                               "cursor": "pointer",
                                               "outline": "none"},
                                    ),
                                    html.Div(
                                        "These tickers create signals "
                                        "for the ticker studied.",
                                        style={"color": PRJCT9_MUTED,
                                               "fontSize": "11px",
                                               "lineHeight": "1.5",
                                               "marginTop": "6px",
                                               "marginBottom": "6px"},
                                    ),
                                    dcc.Dropdown(
                                        id="universe-preset",
                                        options=[
                                            {"label": k, "value": k}
                                            for k in PRIMARY_UNIVERSE_PRESETS.keys()
                                        ],
                                        value="Mega Cap 10",
                                        clearable=False,
                                        style={
                                            "backgroundColor": PRJCT9_BLACK,
                                            "color": PRJCT9_TEXT,
                                            "fontSize": "12px",
                                            "width": "100%",
                                            "boxSizing": "border-box",
                                        },
                                    ),
                                    dcc.Textarea(
                                        id="custom-primaries",
                                        placeholder="AAPL, MSFT, NVDA",
                                        style={**input_style,
                                               "height": "55px",
                                               "fontFamily": "inherit",
                                               "marginTop": "6px",
                                               "width": "100%",
                                               "boxSizing": "border-box"},
                                    ),
                                    html.Button(
                                        "Test 10 signal sources",
                                        id="btn-run",
                                        n_clicks=0,
                                        style={**btn_style,
                                               "marginRight": "0",
                                               "marginTop": "8px",
                                               "marginBottom": "0",
                                               "width": "100%",
                                               "boxSizing": "border-box"},
                                    ),
                                    html.Div(
                                        "Max 10 sources.",
                                        style={"fontSize": "11px",
                                               "color": PRJCT9_MUTED,
                                               "lineHeight": "1.5",
                                               "marginTop": "6px"},
                                    ),
                                ],
                            ),

                            # Step 3: Combined signals (saved studies)
                            html.Div(
                                "3. Combined signals",
                                style={**label_style,
                                       "color": PRJCT9_GREEN,
                                       "fontWeight": "bold",
                                       "marginTop": "14px"},
                            ),
                            html.Div(
                                id="left-combined-status",
                                style={"color": PRJCT9_MUTED,
                                       "fontSize": "11px",
                                       "lineHeight": "1.5",
                                       "marginTop": "2px",
                                       "marginBottom": "6px",
                                       "wordBreak": "break-word"},
                            ),
                            html.Button(
                                "Show combined studies",
                                id="btn-show-combined",
                                n_clicks=0,
                                style={**btn_style,
                                       "marginRight": "0",
                                       "marginBottom": "0",
                                       "width": "100%",
                                       "boxSizing": "border-box"},
                            ),

                            # Step 4: Time windows (Confluence)
                            html.Div(
                                "4. Time windows",
                                style={**label_style,
                                       "color": PRJCT9_GREEN,
                                       "fontWeight": "bold",
                                       "marginTop": "14px"},
                            ),
                            html.Div(
                                id="left-timewindows-status",
                                style={"color": PRJCT9_MUTED,
                                       "fontSize": "11px",
                                       "lineHeight": "1.5",
                                       "marginTop": "2px",
                                       "marginBottom": "6px",
                                       "wordBreak": "break-word"},
                            ),
                            html.Button(
                                "Show time-window check",
                                id="btn-show-time-windows",
                                n_clicks=0,
                                style={**btn_style,
                                       "marginRight": "0",
                                       "marginBottom": "0",
                                       "width": "100%",
                                       "boxSizing": "border-box"},
                            ),

                            # Step 5: Traffic Flow
                            html.Div(
                                "5. Traffic flow",
                                style={**label_style,
                                       "color": PRJCT9_GREEN,
                                       "fontWeight": "bold",
                                       "marginTop": "14px"},
                            ),
                            html.Div(
                                id="left-trafficflow-status",
                                style={"color": PRJCT9_MUTED,
                                       "fontSize": "11px",
                                       "lineHeight": "1.5",
                                       "marginTop": "2px",
                                       "marginBottom": "6px",
                                       "wordBreak": "break-word"},
                            ),
                            html.Button(
                                "Show traffic flow",
                                id="btn-show-traffic-flow",
                                n_clicks=0,
                                style={**btn_style,
                                       "marginRight": "0",
                                       "marginBottom": "0",
                                       "width": "100%",
                                       "boxSizing": "border-box"},
                            ),
                        ],
                    ),

                    # Main: single-screen research cockpit (no tabs).
                    # All engine areas render together as one continuous
                    # dashboard. The Selected Pattern subsection has its
                    # own ID so a separate callback can update it on row
                    # click WITHOUT re-rendering the entire dashboard.
                    html.Div(
                        className="prjct9-main",
                        style=panel_style,
                        children=[
                            # Phase 6C-2: Research Catalogue browser
                            # renders above the per-ticker dashboard
                            # so the user sees what exists across the
                            # whole catalogue before drilling into a
                            # single ticker. Updated by a separate
                            # callback that reads
                            # catalogue-snapshot-store; the dashboard
                            # render below stays untouched.
                            html.Div(
                                id="catalogue-browser-section",
                                style={"padding": "0",
                                       "marginBottom": "10px"},
                            ),
                            dcc.Loading(
                                id="dashboard-loading",
                                type="circle",
                                color=PRJCT9_GREEN,
                                children=html.Div(
                                    id="dashboard-main",
                                    style={"padding": "0",
                                           "minHeight": "300px"},
                                ),
                            ),
                        ],
                    ),
                ],
            ),

            # Stores
            dcc.Store(id="results-store"),
            dcc.Store(id="meta-store"),
            dcc.Store(id="log-store", data=[]),
            dcc.Store(id="selected-row-store"),
            # Phase 6C-1: Catalogue Coverage cache. Holds the per-
            # ticker engine-status snapshot read by the dashboard
            # render. Updated when the studied ticker changes, when
            # the user clicks Refresh catalogue, and after Build
            # missing charts finishes a sweep.
            dcc.Store(id="catalogue-store"),
            # Phase 6C-2: cross-ticker catalogue snapshot. Holds the
            # whole-catalogue summary used by the Research Catalogue
            # browser - top opportunities, targets needing chart
            # data, complete-coverage targets, and the dropdown
            # options. Refreshed when the studied ticker changes,
            # when the user clicks Refresh catalogue index, and
            # after Build missing charts.
            dcc.Store(id="catalogue-snapshot-store"),
            # Tracks the most recent left-rail nav target. Written
            # by clientside scrollIntoView callbacks so the Dash
            # callback graph has a registered output.
            dcc.Store(id="nav-target-store", data=""),
            # Boot trigger: fires once ~300ms after page load to
            # auto-load SPY (Option A from the prompt). After the
            # single fire, max_intervals=1 stops further fires.
            dcc.Interval(
                id="boot-trigger",
                interval=300,
                n_intervals=0,
                max_intervals=1,
            ),
        ],
    )

    # ----------------------------------------------------------------- callbacks

    @app.callback(
        Output("output-discovery-status", "children"),
        Input("target-ticker", "value"),
    )
    def _discovery_status(_target):
        files = _discover_impactsearch_outputs(_output_dir())
        if not files:
            return "No saved ticker studies found yet."
        return f"{len(files)} saved ticker studies."

    @app.callback(
        Output("market-scan-status", "children"),
        Output("left-combined-status", "children"),
        Output("left-timewindows-status", "children"),
        Output("left-trafficflow-status", "children"),
        Input("meta-store", "data"),
    )
    def _left_rail_status(meta):
        meta = meta or {}
        scan_rows = int(meta.get("market_scan_rows") or 0)
        if scan_rows:
            scan_status = f"{scan_rows:,} tickers scanned."
        else:
            scan_status = "No saved market scan found yet."
        stack_n = int(meta.get("stack_runs_for_target") or 0)
        if stack_n == 0:
            combined_status = "No saved combined study yet."
        elif stack_n == 1:
            combined_status = "1 saved combined study."
        else:
            combined_status = f"{stack_n} saved combined studies."
        tf_avail = meta.get("timeframes_available")
        tf_total = meta.get("timeframes_total")
        if tf_avail is not None and tf_total:
            tw_status = f"{tf_avail}/{tf_total} time windows found."
        else:
            tw_status = "Time windows not loaded yet."
        if stack_n:
            tf_traffic_status = (
                f"{stack_n} stack ready."
                if stack_n == 1
                else f"{stack_n} stacks ready."
            )
        else:
            tf_traffic_status = "No stack ready yet."
        return (scan_status, combined_status, tw_status,
                tf_traffic_status)

    @app.callback(
        Output("log-store", "data", allow_duplicate=True),
        Input("btn-market-scan", "n_clicks"),
        Input("btn-show-combined", "n_clicks"),
        Input("btn-show-time-windows", "n_clicks"),
        Input("btn-show-traffic-flow", "n_clicks"),
        State("log-store", "data"),
        prevent_initial_call=True,
    )
    def _left_rail_action_log(_m, _c, _t, _tf, log):
        log = list(log or [])
        trigger = (
            callback_context.triggered[0]["prop_id"].split(".")[0]
            if callback_context.triggered else ""
        )
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if trigger == "btn-market-scan":
            log.append(
                f"[{ts}] market scan opened. See Market Scan section."
            )
        elif trigger == "btn-show-combined":
            log.append(
                f"[{ts}] combined studies opened. See Combined Signals "
                "Detail section."
            )
        elif trigger == "btn-show-time-windows":
            log.append(
                f"[{ts}] time-window check opened. See Time Windows "
                "Detail section."
            )
        elif trigger == "btn-show-traffic-flow":
            log.append(
                f"[{ts}] traffic flow opened. See Traffic Flow section."
            )
        return log[-200:]

    @app.callback(
        Output("log-store", "data", allow_duplicate=True),
        Input("btn-build-chart-data", "n_clicks"),
        State("selected-row-store", "data"),
        State("results-store", "data"),
        State("meta-store", "data"),
        State("log-store", "data"),
        prevent_initial_call=True,
    )
    def _build_chart_data_action(
        _clicks, selected_row, results_data, meta, log,
    ):
        """Phase 6B-1 single-row artifact generator. Builds and saves
        exactly one ``research_day_v1`` artifact for the currently
        selected pattern. Bounded, offline, error-trapped, never
        triggers a 36k-row batch."""
        log = list(log or [])
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        row = selected_row or _auto_select_best_row(
            results_data, meta or {},
        )
        if not isinstance(row, dict):
            log.append(
                f"[{ts}] build chart data: nothing selected; "
                "load a saved ticker study first."
            )
            return log[-200:]
        signal_source = row.get("Primary Ticker") or row.get("primary_ticker")
        target = (
            row.get("Secondary Ticker") or row.get("secondary_ticker")
            or (meta or {}).get("target") or DEFAULT_TARGET
        )
        if not signal_source or not target:
            log.append(
                f"[{ts}] build chart data: missing signal source or "
                "ticker on the selected row."
            )
            return log[-200:]
        signal_source = str(signal_source).strip().upper()
        target = str(target).strip().upper()
        overrides = {
            "total_capture_pct": row.get("Total Capture (%)"),
            "avg_daily_capture_pct": row.get("Avg Daily Capture (%)"),
            "sharpe_ratio": row.get("Sharpe"),
            "trigger_days": row.get("Trigger Days"),
            "wins": row.get("Wins"),
            "losses": row.get("Losses"),
            "p_value": row.get("P-Value"),
            "significant_95": (
                str(row.get("Significant 95%") or "").strip().upper()
                == "YES"
            ),
        }
        try:
            path = _build_research_day_artifact_for_pair(
                signal_source, target,
                summary_overrides=overrides,
            )
        except Exception as exc:
            log.append(
                f"[{ts}] build chart data failed for "
                f"{signal_source} on {target}: "
                f"{type(exc).__name__}: {exc}"
            )
            return log[-200:]
        if path is None:
            log.append(
                f"[{ts}] build chart data: saved local data missing "
                f"for {signal_source} on {target}."
            )
            return log[-200:]
        log.append(
            f"[{ts}] saved chart data for {signal_source} on "
            f"{target}. Re-open the saved ticker study to refresh "
            "the chart."
        )
        return log[-200:]

    @app.callback(
        Output("log-store", "data", allow_duplicate=True),
        Input("btn-build-stack-chart-data", "n_clicks"),
        State("meta-store", "data"),
        State("log-store", "data"),
        prevent_initial_call=True,
    )
    def _build_stack_chart_data_action(_clicks, meta, log):
        """Phase 6B-2 single-row stack artifact generator. Builds and
        saves exactly one ``research_day_v1`` artifact for the top
        leaderboard row of the studied ticker's most recent saved
        StackBuilder run. Bounded, offline, error-trapped."""
        log = list(log or [])
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        target = (meta or {}).get("target") or DEFAULT_TARGET
        target = str(target).strip().upper()
        try:
            path, reason = _build_stack_artifact_for_top_run(target)
        except Exception as exc:
            log.append(
                f"[{ts}] build stack chart data failed for {target}: "
                f"{type(exc).__name__}: {exc}"
            )
            return log[-200:]
        if path is None:
            # Differentiated user-facing copy. Each branch maps to a
            # specific failure mode so the user knows what saved
            # local data is missing.
            if reason == "no_run":
                msg = (
                    f"build stack chart data: no saved combined-"
                    f"signal run found for {target}."
                )
            elif reason == "target_cache_missing":
                msg = (
                    f"build stack chart data: {target} price cache "
                    "missing on this computer."
                )
            elif reason == "no_member_caches":
                msg = (
                    f"build stack chart data: stack member caches "
                    f"are missing for {target}."
                )
            elif reason == "write_failed":
                msg = (
                    f"build stack chart data: chart data built for "
                    f"{target} but the file could not be saved."
                )
            elif reason == "engine_unavailable":
                msg = (
                    "build stack chart data: research engine "
                    "unavailable; restart the launcher."
                )
            else:
                msg = (
                    f"build stack chart data: unknown failure for "
                    f"{target}."
                )
            log.append(f"[{ts}] {msg}")
            return log[-200:]
        log.append(
            f"[{ts}] saved stack chart data for {target}. Re-open "
            "the saved ticker study to refresh the chart."
        )
        return log[-200:]

    @app.callback(
        Output("log-store", "data", allow_duplicate=True),
        Input("btn-build-confluence-chart-data", "n_clicks"),
        State("meta-store", "data"),
        State("log-store", "data"),
        prevent_initial_call=True,
    )
    def _build_confluence_chart_data_action(_clicks, meta, log):
        """Phase 6B-3 single-target confluence artifact generator.
        Builds and saves exactly one ``research_day_v1`` artifact for
        the studied ticker. Bounded, offline, error-trapped; never
        triggers a universe scan."""
        log = list(log or [])
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        target = (meta or {}).get("target") or DEFAULT_TARGET
        target = str(target).strip().upper()
        try:
            path, reason = _build_confluence_artifact_for_target(target)
        except Exception as exc:
            log.append(
                f"[{ts}] build confluence chart data failed for "
                f"{target}: {type(exc).__name__}: {exc}"
            )
            return log[-200:]
        if path is None:
            if reason == "no_libraries":
                msg = (
                    f"build confluence chart data: no saved "
                    f"confluence libraries for {target}."
                )
            elif reason == "target_cache_missing":
                msg = (
                    f"build confluence chart data: {target} price "
                    "cache missing on this computer."
                )
            elif reason == "build_failed":
                msg = (
                    f"build confluence chart data: confluence build "
                    f"failed for {target}."
                )
            elif reason == "write_failed":
                msg = (
                    f"build confluence chart data: chart data built "
                    f"for {target} but the file could not be saved."
                )
            elif reason == "engine_unavailable":
                msg = (
                    "build confluence chart data: research engine "
                    "unavailable; restart the launcher."
                )
            else:
                msg = (
                    f"build confluence chart data: unknown failure "
                    f"for {target}."
                )
            log.append(f"[{ts}] {msg}")
            return log[-200:]
        log.append(
            f"[{ts}] saved confluence chart data for {target}. "
            "Re-open the saved ticker study to refresh the chart."
        )
        return log[-200:]

    @app.callback(
        Output("log-store", "data", allow_duplicate=True),
        Input("btn-build-trafficflow-chart-data", "n_clicks"),
        State("meta-store", "data"),
        State("log-store", "data"),
        prevent_initial_call=True,
    )
    def _build_trafficflow_chart_data_action(_clicks, meta, log):
        """Phase 6B-4 single-row TrafficFlow artifact generator.
        Builds and saves exactly one ``research_day_v1`` artifact
        for the studied ticker's most recent saved StackBuilder
        run. Bounded, offline, error-trapped."""
        log = list(log or [])
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        target = (meta or {}).get("target") or DEFAULT_TARGET
        target = str(target).strip().upper()
        try:
            path, reason = _build_trafficflow_artifact_for_top_run(
                target,
            )
        except Exception as exc:
            log.append(
                f"[{ts}] build traffic flow chart data failed for "
                f"{target}: {type(exc).__name__}: {exc}"
            )
            return log[-200:]
        if path is None:
            if reason == "no_run":
                msg = (
                    f"build traffic flow chart data: no saved "
                    f"combined-signal run found for {target}."
                )
            elif reason == "target_cache_missing":
                msg = (
                    f"build traffic flow chart data: {target} "
                    "price cache missing on this computer."
                )
            elif reason == "no_member_caches":
                msg = (
                    f"build traffic flow chart data: stack member "
                    f"caches are missing for {target}."
                )
            elif reason == "write_failed":
                msg = (
                    f"build traffic flow chart data: chart data built "
                    f"for {target} but the file could not be saved."
                )
            elif reason == "engine_unavailable":
                msg = (
                    "build traffic flow chart data: research engine "
                    "unavailable; restart the launcher."
                )
            else:
                msg = (
                    f"build traffic flow chart data: unknown failure "
                    f"for {target}."
                )
            log.append(f"[{ts}] {msg}")
            return log[-200:]
        log.append(
            f"[{ts}] saved traffic flow chart data for {target}. "
            "Re-open the saved ticker study to refresh the chart."
        )
        return log[-200:]

    # ----------------------------------------------------------------- catalogue
    # Phase 6C-2: cross-ticker catalogue snapshot. Refreshed on
    # ticker-change (uses TTL cache) and on Refresh catalogue index
    # clicks (force_refresh + persist). Build missing charts also
    # writes here via its own multi-output callback below.
    @app.callback(
        Output("catalogue-snapshot-store", "data"),
        Input("meta-store", "data"),
        Input("btn-refresh-catalogue-index", "n_clicks"),
        prevent_initial_call=False,
    )
    def _update_catalogue_snapshot_store(_meta, _refresh_n):
        trigger = (
            callback_context.triggered[0]["prop_id"].split(".")[0]
            if callback_context.triggered else ""
        )
        force = trigger == "btn-refresh-catalogue-index"
        try:
            import research_catalogue as rc
            return rc.get_catalogue_snapshot(
                force_refresh=force,
                persist_if_built=force,
            )
        except Exception:
            return {
                "schema": "research_catalogue_snapshot_v1",
                "counts": {
                    "engine": {},
                    "state": {},
                    "targets_total": 0,
                },
                "targets": [],
                "chart_ready_targets": [],
                "targets_needing_chart_data": [],
                "complete_coverage_targets": [],
                "entries": [],
                "top_opportunities": [],
            }

    # Phase 6C-1: refresh the catalogue store on ticker-change and
    # Refresh-catalogue clicks. Build missing charts owns its own
    # catalogue refresh (the build callback co-writes catalogue-
    # store) so that the post-build snapshot is always fresh; the
    # earlier wiring listened on btn-build-missing-charts AND
    # log-store from this callback as well, which let it re-fire
    # before the build helpers had written their files and cached
    # a stale "Build chart data" snapshot. Inputs here are now
    # only the two events that should refresh independently of
    # the build sweep.
    @app.callback(
        Output("catalogue-store", "data"),
        Input("meta-store", "data"),
        Input("btn-refresh-catalogue", "n_clicks"),
        prevent_initial_call=False,
    )
    def _update_catalogue_store(meta, _refresh_n):
        target = ((meta or {}).get("target") or DEFAULT_TARGET)
        target = str(target).strip().upper() or DEFAULT_TARGET
        trigger = (
            callback_context.triggered[0]["prop_id"].split(".")[0]
            if callback_context.triggered else ""
        )
        force = trigger == "btn-refresh-catalogue"
        try:
            import research_catalogue as rc
            return rc.summarize_ticker_catalogue(
                target, force_refresh=force,
            )
        except Exception:
            return {
                "target": target,
                "statuses": [],
                "totals": {
                    "chart_ready": 0,
                    "saved_research_found": 0,
                    "no_saved_research": 0,
                },
            }

    # Phase 6C-1: unified Build missing charts action. Iterates the
    # five engines in catalogue order and calls the existing single-
    # ticker build helpers ONLY for engines whose state needs and can
    # use a build step. Market scan stays saved-output-only - this
    # callback never invokes a universe-wide OnePass scan.
    #
    # Phase 6C-1 amendment: this callback now also writes
    # catalogue-store so the post-build snapshot is always fresh.
    # Earlier wiring let _update_catalogue_store fire on the same
    # button click and cache the pre-build snapshot before the
    # build helpers wrote their files; the result was a stale
    # "Build chart data" state in Catalogue Coverage even after a
    # successful build. Owning the post-build refresh here
    # eliminates the race - we always summarize AFTER the build
    # loop with force_refresh=True.
    @app.callback(
        Output("log-store", "data", allow_duplicate=True),
        Output("catalogue-store", "data", allow_duplicate=True),
        Output("catalogue-snapshot-store", "data", allow_duplicate=True),
        Input("btn-build-missing-charts", "n_clicks"),
        State("meta-store", "data"),
        State("results-store", "data"),
        State("selected-row-store", "data"),
        State("log-store", "data"),
        prevent_initial_call=True,
    )
    def _build_missing_charts_action(
        _clicks, meta, results_data, selected_row, log,
    ):
        log = list(log or [])
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        target = ((meta or {}).get("target") or DEFAULT_TARGET)
        target = str(target).strip().upper() or DEFAULT_TARGET
        try:
            import research_catalogue as rc
        except Exception as exc:
            log.append(
                f"[{ts}] build missing charts failed: "
                f"{type(exc).__name__}: {exc}"
            )
            # Catalogue module unavailable - leave both stores as is
            # rather than overwriting with a partial snapshot.
            return log[-200:], no_update, no_update
        try:
            summary = rc.summarize_ticker_catalogue(
                target, force_refresh=True,
            )
        except Exception as exc:
            log.append(
                f"[{ts}] build missing charts failed: "
                f"{type(exc).__name__}: {exc}"
            )
            return log[-200:], no_update, no_update
        statuses = summary.get("statuses") or []
        log.append(f"[{ts}] Build missing charts: {target}.")
        for row in statuses:
            engine = row.get("engine")
            state = row.get("state")
            if engine == "market_scan":
                if state == rc.STATE_NO_SAVED_RESEARCH:
                    log.append(
                        f"[{ts}] Market scan: no saved scan to use."
                    )
                else:
                    log.append(
                        f"[{ts}] Market scan: saved scan already "
                        "ready."
                    )
                continue
            if state == rc.STATE_CHART_READY:
                if engine == "impactsearch":
                    msg = "Single-signal chart already ready."
                elif engine == "stackbuilder":
                    msg = "Combined-signal chart already ready."
                elif engine == "confluence":
                    msg = "Time-window chart already ready."
                elif engine == "trafficflow":
                    msg = "Traffic-flow chart already ready."
                else:
                    msg = f"{engine} chart already ready."
                log.append(f"[{ts}] {msg}")
                continue
            if state == rc.STATE_NO_SAVED_RESEARCH:
                if engine == "impactsearch":
                    msg = (
                        "Single-signal chart could not be built: "
                        f"no saved single-signal study for {target}."
                    )
                elif engine == "stackbuilder":
                    msg = (
                        "Combined-signal chart could not be built: "
                        f"no saved combined-signal study for {target}."
                    )
                elif engine == "confluence":
                    msg = (
                        "Time-window chart could not be built: "
                        f"no saved time-window data for {target}."
                    )
                elif engine == "trafficflow":
                    msg = (
                        "Traffic-flow chart could not be built: "
                        f"no saved combined-signal study for {target}."
                    )
                else:
                    msg = f"{engine} chart could not be built."
                log.append(f"[{ts}] {msg}")
                continue
            # state == saved_research_found -> attempt the build.
            if engine == "impactsearch":
                row_for_build = (
                    selected_row
                    or _auto_select_best_row(results_data, meta or {})
                )
                if not isinstance(row_for_build, dict):
                    log.append(
                        f"[{ts}] Single-signal chart could not be "
                        "built: pick a row in Patterns worth a look "
                        "first."
                    )
                    continue
                signal_source = (
                    row_for_build.get("Primary Ticker")
                    or row_for_build.get("primary_ticker")
                )
                row_target = (
                    row_for_build.get("Secondary Ticker")
                    or row_for_build.get("secondary_ticker")
                    or target
                )
                if not signal_source or not row_target:
                    log.append(
                        f"[{ts}] Single-signal chart could not be "
                        "built: signal source or ticker missing on "
                        "the selected pattern."
                    )
                    continue
                signal_source = str(signal_source).strip().upper()
                row_target = str(row_target).strip().upper()
                overrides = {
                    "total_capture_pct": row_for_build.get(
                        "Total Capture (%)",
                    ),
                    "sharpe_ratio": row_for_build.get("Sharpe"),
                    "trigger_days": row_for_build.get("Trigger Days"),
                }
                try:
                    path = _build_research_day_artifact_for_pair(
                        signal_source, row_target,
                        summary_overrides=overrides,
                    )
                except Exception as exc:
                    log.append(
                        f"[{ts}] Single-signal chart could not be "
                        f"built: {type(exc).__name__}: {exc}."
                    )
                    continue
                if path is None:
                    log.append(
                        f"[{ts}] Single-signal chart could not be "
                        f"built: saved local data missing for "
                        f"{signal_source} on {row_target}."
                    )
                else:
                    log.append(
                        f"[{ts}] Single-signal chart built."
                    )
                continue
            if engine == "stackbuilder":
                try:
                    path, reason = _build_stack_artifact_for_top_run(
                        target,
                    )
                except Exception as exc:
                    log.append(
                        f"[{ts}] Combined-signal chart could not be "
                        f"built: {type(exc).__name__}: {exc}."
                    )
                    continue
                if path is not None:
                    log.append(
                        f"[{ts}] Combined-signal chart built."
                    )
                else:
                    log.append(
                        f"[{ts}] Combined-signal chart could not be "
                        f"built: {_reason_text(reason, target)}."
                    )
                continue
            if engine == "confluence":
                try:
                    path, reason = _build_confluence_artifact_for_target(
                        target,
                    )
                except Exception as exc:
                    log.append(
                        f"[{ts}] Time-window chart could not be "
                        f"built: {type(exc).__name__}: {exc}."
                    )
                    continue
                if path is not None:
                    log.append(
                        f"[{ts}] Time-window chart built."
                    )
                else:
                    log.append(
                        f"[{ts}] Time-window chart could not be "
                        f"built: {_reason_text(reason, target)}."
                    )
                continue
            if engine == "trafficflow":
                try:
                    path, reason = _build_trafficflow_artifact_for_top_run(
                        target,
                    )
                except Exception as exc:
                    log.append(
                        f"[{ts}] Traffic-flow chart could not be "
                        f"built: {type(exc).__name__}: {exc}."
                    )
                    continue
                if path is not None:
                    log.append(
                        f"[{ts}] Traffic-flow chart built."
                    )
                else:
                    log.append(
                        f"[{ts}] Traffic-flow chart could not be "
                        f"built: {_reason_text(reason, target)}."
                    )
                continue
        # Phase 6C-1 amendment: re-summarize AFTER the build loop
        # with force_refresh=True so the catalogue-store payload
        # picks up any artifact files the build helpers just wrote.
        # Falling back to the pre-build summary on failure is the
        # right move - it is still better than a noisily-empty
        # snapshot.
        try:
            post_summary = rc.summarize_ticker_catalogue(
                target, force_refresh=True,
            )
        except Exception:
            post_summary = summary
        # Phase 6C-2: also refresh the cross-ticker snapshot so the
        # Research Catalogue browser reflects the new chart-ready
        # rows produced by this sweep. Same race avoidance: own the
        # refresh here rather than letting another callback fire on
        # the same trigger.
        try:
            post_snapshot = rc.get_catalogue_snapshot(force_refresh=True)
        except Exception:
            post_snapshot = no_update
        return log[-200:], post_summary, post_snapshot

    # Clientside scroll-into-view callbacks for the three left-rail
    # navigate buttons. They run in the browser (no server hop) and
    # take the user directly to the target detail section. The
    # callback writes a no-op string into a hidden store so Dash's
    # callback graph has a registered output; the real effect is the
    # scrollIntoView side-effect.
    app.clientside_callback(
        """
        function(n) {
            if (!n) { return ''; }
            var el = document.getElementById('market-scan-section');
            if (el && el.scrollIntoView) {
                el.scrollIntoView({behavior: 'smooth', block: 'start'});
            }
            return 'market-scan';
        }
        """,
        Output("nav-target-store", "data"),
        Input("btn-market-scan", "n_clicks"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        """
        function(n) {
            if (!n) { return ''; }
            var el = document.getElementById('combined-signals-detail');
            if (el && el.scrollIntoView) {
                el.scrollIntoView({behavior: 'smooth', block: 'start'});
            }
            return 'combined-signals';
        }
        """,
        Output("nav-target-store", "data", allow_duplicate=True),
        Input("btn-show-combined", "n_clicks"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        """
        function(n) {
            if (!n) { return ''; }
            var el = document.getElementById('time-windows-detail');
            if (el && el.scrollIntoView) {
                el.scrollIntoView({behavior: 'smooth', block: 'start'});
            }
            return 'time-windows';
        }
        """,
        Output("nav-target-store", "data", allow_duplicate=True),
        Input("btn-show-time-windows", "n_clicks"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        """
        function(n) {
            if (!n) { return ''; }
            var el = document.getElementById('traffic-flow-detail');
            if (el && el.scrollIntoView) {
                el.scrollIntoView({behavior: 'smooth', block: 'start'});
            }
            return 'traffic-flow';
        }
        """,
        Output("nav-target-store", "data", allow_duplicate=True),
        Input("btn-show-traffic-flow", "n_clicks"),
        prevent_initial_call=True,
    )

    # Phase 6C-2: copy a catalogue-target-dropdown selection into the
    # ticker input box so the user can see what they picked. The
    # dropdown also fires the load action via _on_action's
    # catalogue-target-dropdown Input below.
    @app.callback(
        Output("target-ticker", "value"),
        Input("catalogue-target-dropdown", "value"),
        prevent_initial_call=True,
    )
    def _propagate_dropdown_to_ticker_input(value):
        if not value:
            return no_update
        return str(value).strip().upper()

    @app.callback(
        Output("results-store", "data"),
        Output("meta-store", "data"),
        Output("log-store", "data"),
        Input("btn-load", "n_clicks"),
        Input("btn-run", "n_clicks"),
        Input("boot-trigger", "n_intervals"),
        Input("catalogue-target-dropdown", "value"),
        State("target-ticker", "value"),
        State("universe-preset", "value"),
        State("custom-primaries", "value"),
        State("log-store", "data"),
        State("results-store", "data"),
        prevent_initial_call=True,
    )
    def _on_action(
        _load_n, _run_n, boot_n, dropdown_value, target, preset, custom_text,
        log, current_results,
    ):
        log = list(log or [])
        target = (target or "").strip().upper() or DEFAULT_TARGET
        trigger = (
            callback_context.triggered[0]["prop_id"].split(".")[0]
            if callback_context.triggered else ""
        )
        # Phase 6C-2: catalogue dropdown fires this callback like a
        # "load" click. Use the dropdown value as the target so the
        # Catalogue Browser jumps straight to the picked ticker
        # without a second user step.
        if trigger == "catalogue-target-dropdown" and dropdown_value:
            target = str(dropdown_value).strip().upper() or DEFAULT_TARGET
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # The "Run quick study" button always implies the live (capped)
        # path; "Show saved study" always implies browse. The legacy
        # mode-radio is gone, so universe-resolution treats every
        # quick-study click as live-mode (caps at MAX_PRIMARIES_LIVE).
        primaries = _primary_universe_from_preset(
            preset, custom_text, live_mode=(trigger == "btn-run"),
        )
        meta: dict = {
            "target": target,
            "preset": preset,
            "primaries": primaries,
            "loaded_path": None,
            "has_validation": False,
            "live_enabled": True,
            "live_run": False,
        }

        def _do_load(quiet: bool = False):
            # Look across all engine modes for this ticker so the
            # cockpit can show real status rather than asking the
            # user to pick a mode first.
            stack_runs_all = _discover_stack_runs(_stack_output_dir())
            stack_runs_for_target = [
                r for r in stack_runs_all
                if r["ticker"].upper() == target.upper()
            ]
            tf_coverage = _timeframe_coverage_for_ticker(
                target, _signal_library_dir(),
            )
            tf_available_count = sum(1 for r in tf_coverage if r["available"])
            meta["stack_runs_for_target"] = len(stack_runs_for_target)
            meta["timeframes_available"] = int(tf_available_count)
            meta["timeframes_total"] = int(len(tf_coverage))
            # OnePass / Market Scan availability + headline summary
            scan_files = _discover_onepass_outputs()
            if scan_files:
                scan_summary = _load_onepass_summary(scan_files[0])
                meta["market_scan_path"] = str(scan_files[0])
                meta["market_scan_rows"] = (
                    int((scan_summary or {}).get("rows") or 0)
                )
            else:
                meta["market_scan_path"] = None
                meta["market_scan_rows"] = 0

            xlsx = _resolve_xlsx_for_target(target, _output_dir())
            if xlsx is None:
                meta["loaded_path"] = None
                meta["has_validation"] = False
                if not quiet:
                    log.append(
                        f"[{ts}] no saved research found for '{target}'."
                    )
                # Even when there's no single-signal file, the meta
                # carries discovered stack/timeframe counts so Overview
                # can still render their status.
                return [], meta, log[-200:]
            df_raw = _load_impactsearch_xlsx(xlsx)
            if df_raw.empty:
                log.append(
                    f"[{ts}] saved research for '{target}' returned no rows."
                )
                return [], meta, log[-200:]
            df_norm = _normalize_results_frame(df_raw)
            sidecar = _read_sidecar(xlsx)
            meta["loaded_path"] = str(xlsx)
            meta["has_validation"] = bool(sidecar)
            log.append(
                f"[{ts}] research loaded for {target}: {len(df_norm)} rows."
            )
            data = df_norm.to_dict("records")
            return data, meta, log[-200:]

        # Boot trigger: auto-load SPY once on page load if no results yet.
        if trigger == "boot-trigger":
            if current_results:
                return no_update, no_update, no_update
            return _do_load(quiet=True)

        # "Show saved study" -> always browse path
        if trigger == "btn-load":
            return _do_load(quiet=False)

        # Catalogue dropdown -> load the picked ticker.
        if trigger == "catalogue-target-dropdown":
            return _do_load(quiet=False)

        # "Run quick study" -> always live path
        if trigger == "btn-run":
            if not primaries:
                log.append(
                    f"[{ts}] live test aborted: no signal sources "
                    "supplied. Add tickers under "
                    "'Signal sources for live test' on the left."
                )
                return None, meta, log[-200:]
            engine_status = _live_engine_status()
            log.extend(_format_run_log_start(
                target, primaries, engine_status["preloaded"], ts,
            ))
            outcome = _run_live_preview(target, primaries)
            if not outcome["ok"]:
                log.extend(_format_run_log_failure(
                    outcome["elapsed_seconds"],
                    outcome["error"],
                    ts,
                ))
                return None, meta, log[-200:]
            rows = outcome["rows"] or []
            if not rows:
                elapsed_seconds = outcome["elapsed_seconds"] or 0.0
                log.extend(_format_run_log_success(
                    target, 0, elapsed_seconds, 0, ts,
                ))
                log.append(
                    f"[{ts}]   note: no rows came back. The chosen "
                    "signal sources may not have a saved study yet."
                )
                return [], {**meta, "live_run": True}, log[-200:]
            df_live = pd.DataFrame(rows)
            df_norm = _normalize_results_frame(df_live)
            elapsed_seconds = outcome["elapsed_seconds"] or 0.0
            fastpath_count = _count_fastpath_rows(rows)
            log.extend(_format_run_log_success(
                target, len(df_norm), elapsed_seconds, fastpath_count, ts,
            ))
            meta["live_run"] = True
            return df_norm.to_dict("records"), meta, log[-200:]

        return no_update, no_update, log[-200:]

    # Selected-row store: listens to the Results table's derived state
    # so selection follows sorting/filtering. Listening to results-store
    # lets us clear the selection when a new dataset is loaded.
    @app.callback(
        Output("selected-row-store", "data"),
        Input("results-table", "derived_virtual_selected_rows"),
        Input("results-table", "derived_virtual_data"),
        Input("results-store", "data"),
        prevent_initial_call=True,
    )
    def _store_selected_row(selected_rows, virtual_data, _results_data):
        ctx = callback_context
        triggered = (
            ctx.triggered[0]["prop_id"].split(".")[0]
            if ctx.triggered else ""
        )
        if triggered == "results-store":
            return None
        row = _selected_row_from_table_state(virtual_data, selected_rows)
        if row is None:
            return no_update
        return row

    # Single-screen dashboard: NO tabs, NO mode-radio, NO per-tab dispatch.
    # The whole research cockpit renders as one continuous panel. The
    # Selected Pattern subsection has its own ID and a separate callback
    # so picking a row in the Best Patterns table updates only that
    # subsection (the dashboard does NOT recreate the table on click).
    @app.callback(
        Output("dashboard-main", "children"),
        Input("results-store", "data"),
        Input("meta-store", "data"),
        Input("log-store", "data"),
        Input("catalogue-store", "data"),
        State("selected-row-store", "data"),
    )
    def _render_dashboard(
        results_data, meta, log, catalogue_data, selected_row,
    ):
        meta = meta or {}
        return _render_research_cockpit(
            results_data, meta, log or [], selected_row,
            catalogue_summary=catalogue_data,
        )

    # Phase 6C-2: catalogue browser section. Reads
    # catalogue-snapshot-store and renders the cross-ticker overview
    # (top opportunities, targets needing chart data, complete-
    # coverage targets, target dropdown). Lives ABOVE the per-ticker
    # dashboard so the user sees what's saved across the catalogue
    # before diving into one ticker.
    @app.callback(
        Output("catalogue-browser-section", "children"),
        Input("catalogue-snapshot-store", "data"),
    )
    def _render_catalogue_browser_section(snapshot):
        return _render_catalogue_browser(snapshot)

    # Update the dropdown options whenever the snapshot refreshes.
    # Keeping options in sync with the snapshot lets a Refresh-
    # catalogue-index click (or a successful Build missing charts
    # sweep) immediately surface newly-discovered tickers in the
    # selector without a page reload.
    @app.callback(
        Output("catalogue-target-dropdown", "options"),
        Input("catalogue-snapshot-store", "data"),
    )
    def _update_catalogue_dropdown_options(snapshot):
        snapshot = snapshot or {}
        targets = list(snapshot.get("targets") or [])
        chart_ready = set(snapshot.get("chart_ready_targets") or [])
        # Sort: chart-ready first, then alphabetic for stability.
        targets_sorted = sorted(
            targets,
            key=lambda t: (0 if t in chart_ready else 1, str(t).upper()),
        )
        options = []
        for t in targets_sorted:
            label = str(t)
            if t in chart_ready:
                label = f"{t}  -  chart ready"
            options.append({"label": label, "value": t})
        return options

    # Independent callback that updates ONLY the Selected Pattern card.
    # selected-row-store is an Input here (so clicking a row repaints
    # the card) but the dashboard callback above keeps it as State.
    @app.callback(
        Output("selected-pattern-body", "children"),
        Input("selected-row-store", "data"),
        State("results-store", "data"),
        State("meta-store", "data"),
    )
    def _render_selected_pattern_only(selected_row, results_data, meta):
        meta = meta or {}
        # If no explicit selection yet, fall back to the auto-selected
        # best row so the Selected Pattern subsection always shows
        # content.
        row = selected_row or _auto_select_best_row(results_data, meta)
        return _render_selected_row_card(row, meta)

    def _auto_select_best_row(results_data, meta):
        """Pick the most useful row to display in Selected Pattern when
        the user has not clicked anything. Uses the same sort rule as
        the Best Patterns "Rows worth a look" table: Significant 95%
        Yes first, then Sharpe desc, Trigger Days desc, Total Capture
        desc. Returns None when no rows exist."""
        if not results_data:
            return None
        df = pd.DataFrame(results_data)
        if df.empty:
            return None
        try:
            interesting = _overview_interesting_rows(df, top_n=1)
        except Exception:
            return None
        if interesting is None or interesting.empty:
            return df.iloc[0].to_dict()
        return interesting.iloc[0].to_dict()

    def _section_wrapper(section_id, title, subtitle, body_children):
        """Wrap a dashboard section as a self-contained cockpit panel.
        Each section gets a compact green header + plain subtitle +
        body container, so the cockpit grid can place panels side by
        side without bleeding rules between them."""
        header = html.Div(
            style={"display": "flex",
                   "alignItems": "baseline",
                   "gap": "8px",
                   "flexWrap": "wrap",
                   "marginBottom": "6px"},
            children=[
                html.Span(
                    title,
                    style={"color": PRJCT9_GREEN,
                           "letterSpacing": "2px",
                           "fontSize": "11px",
                           "fontWeight": "bold"},
                ),
                html.Span(
                    subtitle,
                    style={"color": PRJCT9_MUTED,
                           "fontSize": "10px",
                           "lineHeight": "1.4"},
                ),
            ],
        )
        return html.Div(
            id=section_id,
            className="prjct9-cockpit-panel",
            children=[
                header,
                html.Div(
                    className="prjct9-cockpit-panel-body",
                    children=body_children,
                ),
            ],
        )

    def _render_dashboard_header(target, results_data, meta):
        """Render the first-view header zone: title, the one-sentence
        engine explanation, and the four 'At a glance' cards. The cards
        replace the old status chip strip with a richer summary that
        carries both a value and a plain-language line per card."""
        target_upper = str(target).upper() if target else "?"
        rows = len(results_data) if results_data else 0
        stack_n = int((meta or {}).get("stack_runs_for_target") or 0)
        tf_avail = (meta or {}).get("timeframes_available")
        tf_total = (meta or {}).get("timeframes_total")

        def _fmt_int(n):
            try:
                return f"{int(n):,}"
            except Exception:
                return str(n)

        title = html.Div(
            f"{target_upper} Research Snapshot",
            style={"color": PRJCT9_GREEN,
                   "fontSize": "16px",
                   "fontWeight": "bold",
                   "letterSpacing": "1px",
                   "marginBottom": "4px"},
        )
        explainer = html.Div(
            f"For {target_upper}, find signals that came before "
            f"{target_upper} moves.",
            id="engine-explainer-sentence",
            style={"color": PRJCT9_TEXT,
                   "fontSize": "12px",
                   "lineHeight": "1.5",
                   "marginBottom": "8px",
                   "maxWidth": "100%",
                   "boxSizing": "border-box",
                   "wordBreak": "normal",
                   "overflowWrap": "break-word",
                   "hyphens": "none"},
        )

        # Compact at-a-glance card copy. Mobile 2x2 cards have ~168px
        # of inner content width; values + descriptions are kept short
        # so the right-column cards never clip at 390px viewport.
        scan_rows = int((meta or {}).get("market_scan_rows") or 0)
        if scan_rows:
            scan_value = f"{_fmt_int(scan_rows)} tickers"
            scan_tone = "ready"
        else:
            scan_value = "Not loaded yet"
            scan_tone = "neutral"

        if rows:
            patterns_value = f"{_fmt_int(rows)} patterns"
            patterns_tone = "ready"
        else:
            patterns_value = "none yet"
            patterns_tone = "neutral"

        if stack_n:
            stack_value = (
                f"{stack_n} study" if stack_n == 1
                else f"{stack_n} studies"
            )
            stack_tone = "ready"
        else:
            stack_value = "none yet"
            stack_tone = "neutral"

        if tf_avail is not None and tf_total:
            tw_value = f"{tf_avail}/{tf_total} views"
            tw_tone = "ready" if tf_avail == tf_total else "partial"
        else:
            tw_value = "none yet"
            tw_tone = "neutral"

        cards = [
            ("Market scan",
             scan_value,
             "Find outliers",
             scan_tone),
            ("Ticker study",
             patterns_value,
             f"Signals tested against {target_upper}",
             patterns_tone),
            ("Combined signals",
             stack_value,
             "Signals blended together",
             stack_tone),
            ("Time windows",
             tw_value,
             "Daily to yearly",
             tw_tone),
        ]

        def _card(label, value, line, tone):
            value_color = (
                PRJCT9_GREEN if tone in ("ready", "partial") else PRJCT9_TEXT
            )
            return html.Div(
                style={
                    "border": f"1px solid {PRJCT9_BORDER}",
                    "padding": "8px 10px",
                    "backgroundColor": PRJCT9_DIM,
                    "minWidth": "0",
                },
                children=[
                    html.Div(label,
                             style={"color": PRJCT9_MUTED,
                                    "fontSize": "10px",
                                    "letterSpacing": "1px",
                                    "textTransform": "uppercase",
                                    "marginBottom": "2px"}),
                    html.Div(value,
                             style={"color": value_color,
                                    "fontSize": "13px",
                                    "fontWeight": "bold",
                                    "overflowWrap": "anywhere"}),
                    html.Div(line,
                             style={"color": PRJCT9_TEXT,
                                    "fontSize": "11px",
                                    "lineHeight": "1.4",
                                    "marginTop": "3px",
                                    "wordBreak": "normal",
                                    "overflowWrap": "break-word",
                                    "hyphens": "none"}),
                ],
            )

        glance_label = html.Div(
            "AT A GLANCE",
            style={"color": PRJCT9_GREEN,
                   "letterSpacing": "2px",
                   "fontSize": "11px",
                   "fontWeight": "bold",
                   "marginBottom": "4px"},
        )
        glance_grid = html.Div(
            id="at-a-glance-cards",
            className="prjct9-glance-grid",
            children=[_card(*c) for c in cards],
        )

        return html.Div(
            id="dashboard-top",
            style={"maxWidth": "100%", "boxSizing": "border-box",
                   "overflowWrap": "break-word"},
            children=[
                title,
                explainer,
                glance_label,
                glance_grid,
                html.Div(
                    "Scan markets, study a ticker, blend signals, "
                    "check time windows.",
                    id="at-a-glance-helper",
                    className="prjct9-glance-helper",
                    style={"color": PRJCT9_MUTED,
                           "fontSize": "11px",
                           "lineHeight": "1.5",
                           "marginTop": "4px",
                           "marginBottom": "8px",
                           "wordBreak": "normal",
                           "overflowWrap": "normal",
                           "hyphens": "none"},
                ),
            ],
        )

    def _render_best_pattern_summary(results_data, meta):
        """Compact first-view Best Pattern Summary panel: 4 metric
        cells + one small chart (top 8). No table — the table lives
        in the Patterns worth a look detail section below."""
        body: list = []
        if not results_data:
            body.append(html.Div(
                children=[
                    html.Div("No saved research found for this "
                             "ticker yet.",
                             style={"fontSize": "12px",
                                    "marginBottom": "4px"}),
                    html.Div("Try SPY, QQQ, AAPL, or BTC-USD.",
                             style={"color": PRJCT9_MUTED,
                                    "fontSize": "11px"}),
                ],
                style={"padding": "6px 0"},
            ))
            return _section_wrapper(
                "best-pattern-summary",
                "BEST PATTERN SUMMARY",
                "quick read",
                body,
            )

        df = pd.DataFrame(results_data)
        summary = _result_summary(df)

        def _fmt(v, fmt=".2f"):
            if v is None:
                return "-"
            try:
                return format(float(v), fmt)
            except Exception:
                return str(v)

        cells = [
            (
                "Best historical move",
                f"{_fmt(summary.get('best_total_capture'))}%"
                + (f"  ({summary['best_total_capture_primary']})"
                   if summary.get("best_total_capture_primary") else ""),
            ),
            ("Median Sharpe Ratio", _fmt(summary.get("median_sharpe"))),
            ("95% Confidence",
             str(summary.get("significant_95_count", 0))),
            ("Small sample", str(summary.get("fragile_count", 0))),
        ]
        body.append(html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "repeat(2, minmax(0, 1fr))",
                "gap": "6px",
                "marginBottom": "8px",
            },
            children=[
                html.Div(
                    style={"backgroundColor": PRJCT9_DIM,
                           "border": f"1px solid {PRJCT9_BORDER}",
                           "padding": "6px 8px"},
                    children=[
                        html.Div(label,
                                 style={"color": PRJCT9_MUTED,
                                        "fontSize": "9px",
                                        "letterSpacing": "1px",
                                        "textTransform": "uppercase"}),
                        html.Div(value,
                                 style={"color": PRJCT9_GREEN,
                                        "fontSize": "12px",
                                        "fontWeight": "bold",
                                        "marginTop": "2px",
                                        "wordBreak": "break-word"}),
                    ],
                )
                for label, value in cells
            ],
        ))

        body.append(_overview_chart_top_n_capture(df, top_n=6))
        return _section_wrapper(
            "best-pattern-summary",
            "BEST PATTERN SUMMARY",
            "quick read",
            body,
        )

    def _render_catalogue_browser(snapshot):
        """Phase 6C-2 Research Catalogue browser.

        Renders three lists fed by the cross-ticker snapshot:
          * Best chart-ready research      - top_opportunities table
          * Strong saved research that
            needs charts                   - targets_needing_chart_data
          * Targets with complete coverage - complete_coverage_targets
        Plus a target search/selector dropdown sourced from
        snapshot["targets"]. The dropdown's options are populated by
        a separate callback so the snapshot refresh stays in one
        place.
        """
        snapshot = snapshot or {}
        top_opps = list(snapshot.get("top_opportunities") or [])
        needing = list(snapshot.get("targets_needing_chart_data") or [])
        complete = list(snapshot.get("complete_coverage_targets") or [])
        chart_ready_targets = list(
            snapshot.get("chart_ready_targets") or []
        )
        counts = snapshot.get("counts") or {}
        engine_counts = counts.get("engine") or {}
        state_counts = counts.get("state") or {}

        header = html.Div(
            style={"display": "flex",
                   "alignItems": "baseline",
                   "gap": "8px",
                   "flexWrap": "wrap",
                   "marginBottom": "6px"},
            children=[
                html.Span(
                    "RESEARCH CATALOGUE",
                    style={"color": PRJCT9_GREEN,
                           "letterSpacing": "2px",
                           "fontSize": "12px",
                           "fontWeight": "bold"},
                ),
                html.Span(
                    "what PRJCT9 has saved across all studied "
                    "tickers",
                    style={"color": PRJCT9_MUTED,
                           "fontSize": "10px",
                           "lineHeight": "1.4"},
                ),
            ],
        )

        sort_caption = html.Div(
            "Sorted to put chart-ready, high-signal research first.",
            id="catalogue-browser-sort-caption",
            style={"color": PRJCT9_TEXT,
                   "fontSize": "11px",
                   "lineHeight": "1.5",
                   "marginBottom": "8px"},
        )

        # Compact totals strip so the user can see the whole
        # catalogue at a glance.
        totals_text = (
            f"{int(state_counts.get('chart_ready') or 0)} chart-ready "
            "/ "
            f"{int(state_counts.get('saved_research_found') or 0)} "
            "to build / "
            f"{int(counts.get('targets_total') or 0)} tickers"
        )
        totals_div = html.Div(
            totals_text,
            id="catalogue-browser-totals",
            style={"color": PRJCT9_GREEN,
                   "fontSize": "10px",
                   "letterSpacing": "1px",
                   "textTransform": "uppercase",
                   "marginBottom": "8px"},
        )

        # Target search/selector dropdown. Options are filled in by
        # the catalogue-target-dropdown callback so the menu stays in
        # sync with the snapshot.
        dropdown = dcc.Dropdown(
            id="catalogue-target-dropdown",
            options=[],
            value=None,
            placeholder="Open a saved ticker from the catalogue ...",
            clearable=True,
            searchable=True,
            style={
                "backgroundColor": PRJCT9_BLACK,
                "color": PRJCT9_TEXT,
                "fontSize": "12px",
                "marginBottom": "10px",
            },
        )

        body: list = [header, sort_caption, totals_div, dropdown]

        def _opp_table(rows: list[dict]):
            if not rows:
                return html.Div(
                    "No chart-ready research yet. Build chart data "
                    "for a ticker to populate this list.",
                    style={"color": PRJCT9_MUTED,
                           "fontSize": "11px",
                           "lineHeight": "1.5",
                           "marginBottom": "8px"},
                )
            display_rows = []
            for r in rows:
                eng_label = str(r.get("label") or r.get("engine") or "")
                target = str(r.get("target_ticker") or "")
                source = r.get("signal_source") or ""
                if r.get("engine") == "stackbuilder":
                    K = r.get("K")
                    source = (f"K={K}" if K is not None else "")
                if r.get("engine") in ("trafficflow", "stackbuilder"):
                    rid = r.get("run_id") or ""
                    if rid:
                        source = (
                            f"{source} - {rid}" if source else str(rid)
                        )
                cap = r.get("total_capture_pct")
                sharpe = r.get("sharpe_ratio")
                td = r.get("trigger_days")
                sig95 = r.get("significant_95")
                display_rows.append({
                    "Ticker": target,
                    "Engine": eng_label,
                    "Source": source,
                    "Total Capture (%)": (
                        f"{float(cap):.2f}" if cap is not None else "-"
                    ),
                    "Sharpe Ratio": (
                        f"{float(sharpe):.2f}"
                        if sharpe is not None else "-"
                    ),
                    "Signal days": (
                        str(int(td)) if td is not None else "-"
                    ),
                    "95% Confidence": (
                        "Yes" if sig95 is True
                        else ("No" if sig95 is False else "-")
                    ),
                })
            cols = [
                "Ticker", "Engine", "Source",
                "Total Capture (%)", "Sharpe Ratio", "Signal days",
                "95% Confidence",
            ]
            return dash_table.DataTable(
                id="catalogue-browser-top-opportunities",
                columns=[{"name": c, "id": c} for c in cols],
                data=display_rows,
                style_table={"overflowX": "auto"},
                style_cell={
                    "backgroundColor": PRJCT9_BLACK,
                    "color": PRJCT9_TEXT,
                    "fontFamily": "Consolas, 'Courier New', monospace",
                    "fontSize": "11px",
                    "padding": "4px 8px",
                    "border": f"1px solid {PRJCT9_BORDER}",
                    "textAlign": "left",
                },
                style_header={
                    "backgroundColor": PRJCT9_DIM,
                    "color": PRJCT9_GREEN,
                    "fontWeight": "bold",
                    "border": f"1px solid {PRJCT9_GREEN}",
                    "letterSpacing": "1px",
                    "fontSize": "10px",
                },
                style_data_conditional=[
                    {"if": {
                        "filter_query": '{95% Confidence} = "Yes"',
                        "column_id": "95% Confidence",
                    },
                     "color": PRJCT9_GREEN, "fontWeight": "bold"},
                ],
            )

        body.append(html.Div(
            "Best chart-ready research",
            id="catalogue-browser-best-heading",
            style={"color": PRJCT9_GREEN,
                   "fontSize": "11px",
                   "letterSpacing": "1px",
                   "textTransform": "uppercase",
                   "marginTop": "4px",
                   "marginBottom": "4px",
                   "fontWeight": "bold"},
        ))
        body.append(_opp_table(top_opps))

        # "Strong saved research that needs charts"
        body.append(html.Div(
            "Strong saved research that needs charts",
            id="catalogue-browser-needing-heading",
            style={"color": PRJCT9_GREEN,
                   "fontSize": "11px",
                   "letterSpacing": "1px",
                   "textTransform": "uppercase",
                   "marginTop": "10px",
                   "marginBottom": "4px",
                   "fontWeight": "bold"},
        ))
        if not needing:
            body.append(html.Div(
                "No saved-only tickers waiting for chart data.",
                style={"color": PRJCT9_MUTED,
                       "fontSize": "11px",
                       "lineHeight": "1.5"},
            ))
        else:
            body.append(html.Div(
                ", ".join(needing),
                id="catalogue-browser-needing-list",
                style={"color": PRJCT9_TEXT,
                       "fontSize": "11px",
                       "lineHeight": "1.5",
                       "wordBreak": "break-word"},
            ))

        # "Targets with complete coverage"
        body.append(html.Div(
            "Targets with complete coverage",
            id="catalogue-browser-complete-heading",
            style={"color": PRJCT9_GREEN,
                   "fontSize": "11px",
                   "letterSpacing": "1px",
                   "textTransform": "uppercase",
                   "marginTop": "10px",
                   "marginBottom": "4px",
                   "fontWeight": "bold"},
        ))
        if not complete:
            body.append(html.Div(
                "No tickers yet have chart-ready research in every "
                "engine. Build chart data to fill the gaps.",
                style={"color": PRJCT9_MUTED,
                       "fontSize": "11px",
                       "lineHeight": "1.5"},
            ))
        else:
            body.append(html.Div(
                ", ".join(complete),
                id="catalogue-browser-complete-list",
                style={"color": PRJCT9_GREEN,
                       "fontSize": "11px",
                       "lineHeight": "1.5",
                       "fontWeight": "bold",
                       "wordBreak": "break-word"},
            ))

        # Per-engine count strip so the user can see what kinds of
        # research dominate the catalogue at a glance.
        engine_strip_cells = []
        engine_label_map = {
            "market_scan": "Market scans",
            "impactsearch": "Single-signal studies",
            "stackbuilder": "Combined-signal studies",
            "confluence": "Time-window studies",
            "trafficflow": "Traffic-flow studies",
        }
        for engine, label in engine_label_map.items():
            n = int(engine_counts.get(engine) or 0)
            engine_strip_cells.append(html.Div(
                style={"backgroundColor": PRJCT9_DIM,
                       "border": f"1px solid {PRJCT9_BORDER}",
                       "padding": "4px 8px",
                       "minWidth": "0"},
                children=[
                    html.Div(label,
                             style={"color": PRJCT9_MUTED,
                                    "fontSize": "9px",
                                    "letterSpacing": "1px",
                                    "textTransform": "uppercase"}),
                    html.Div(str(n),
                             style={"color": PRJCT9_GREEN,
                                    "fontSize": "12px",
                                    "fontWeight": "bold",
                                    "marginTop": "2px"}),
                ],
            ))
        body.append(html.Div(
            id="catalogue-browser-engine-strip",
            style={"display": "grid",
                   "gridTemplateColumns":
                       "repeat(auto-fit, minmax(120px, 1fr))",
                   "gap": "6px",
                   "marginTop": "10px"},
            children=engine_strip_cells,
        ))

        return html.Div(
            id="catalogue-browser-panel",
            className="prjct9-cockpit-panel",
            children=body,
        )

    def _render_catalogue_coverage(meta, catalogue_summary):
        """Phase 6C-1: Catalogue Coverage panel.

        Reads a per-engine status snapshot from
        ``research_catalogue.summarize_ticker_catalogue`` and renders
        one compact row per engine: Market scan / Single signals /
        Combined signals / Time windows / Traffic flow. Each row
        shows the plain-English state ("Chart ready", "Saved
        research found", "Build chart data", "No saved research yet")
        plus the engine's short message.

        ``catalogue_summary`` is the dict produced by the catalogue
        module. When ``None`` the panel still renders by computing
        the snapshot in line so the first paint never blanks.
        """
        meta = meta or {}
        target = (meta.get("target") or DEFAULT_TARGET).strip().upper()

        if not catalogue_summary or catalogue_summary.get("target") != target:
            try:
                import research_catalogue as rc
                catalogue_summary = rc.summarize_ticker_catalogue(target)
            except Exception:
                catalogue_summary = {
                    "target": target,
                    "statuses": [],
                    "totals": {
                        "chart_ready": 0,
                        "saved_research_found": 0,
                        "no_saved_research": 0,
                    },
                }

        try:
            import research_catalogue as rc_mod
            STATE_CHART_READY = rc_mod.STATE_CHART_READY
            STATE_SAVED_RESEARCH_FOUND = rc_mod.STATE_SAVED_RESEARCH_FOUND
            STATE_NO_SAVED_RESEARCH = rc_mod.STATE_NO_SAVED_RESEARCH
        except Exception:
            STATE_CHART_READY = "chart_ready"
            STATE_SAVED_RESEARCH_FOUND = "saved_research_found"
            STATE_NO_SAVED_RESEARCH = "no_saved_research"

        statuses = list(catalogue_summary.get("statuses") or [])
        totals = catalogue_summary.get("totals") or {}

        body: list = []
        body.append(html.Div(
            "PRJCT9 checks saved market research, signal studies, "
            "combined signals, time windows, and pressure history "
            "for this ticker.",
            id="catalogue-coverage-explainer",
            style={"color": PRJCT9_TEXT,
                   "fontSize": "11px",
                   "lineHeight": "1.5",
                   "marginBottom": "8px"},
        ))

        def _state_label(state, count):
            if state == STATE_CHART_READY:
                return "Chart ready", PRJCT9_GREEN
            if state == STATE_SAVED_RESEARCH_FOUND:
                return "Build chart data", PRJCT9_TEXT
            if state == STATE_NO_SAVED_RESEARCH:
                return "No saved research yet", PRJCT9_MUTED
            return "Status unknown", PRJCT9_MUTED

        def _row(status):
            label = str(status.get("label") or "")
            state = str(status.get("state") or "")
            count = status.get("count")
            message = str(status.get("message") or "")
            state_text, color = _state_label(state, count)
            count_chip = ""
            try:
                if count is not None:
                    n = int(count)
                    if n > 0:
                        count_chip = f"  ({n})"
            except (TypeError, ValueError):
                count_chip = ""
            engine_id = str(status.get("engine") or "").strip()
            row_id = (
                f"catalogue-row-{engine_id}" if engine_id
                else "catalogue-row-unknown"
            )
            return html.Div(
                id=row_id,
                className=(
                    "prjct9-catalogue-row "
                    f"prjct9-catalogue-state-{state}"
                ),
                style={
                    "padding": "6px 0",
                    "borderBottom": f"1px solid {PRJCT9_BORDER}",
                },
                children=[
                    html.Div(
                        style={"display": "flex",
                               "justifyContent": "space-between",
                               "gap": "8px",
                               "flexWrap": "wrap"},
                        children=[
                            html.Span(label,
                                      style={"color": PRJCT9_GREEN,
                                             "fontSize": "10px",
                                             "letterSpacing": "1px",
                                             "textTransform": "uppercase",
                                             "fontWeight": "bold"}),
                            html.Span(
                                state_text + count_chip,
                                style={"color": color,
                                       "fontSize": "11px",
                                       "fontWeight": "bold"},
                            ),
                        ],
                    ),
                    html.Div(message,
                             style={"color": PRJCT9_TEXT,
                                    "fontSize": "11px",
                                    "lineHeight": "1.5",
                                    "marginTop": "2px",
                                    "wordBreak": "break-word"}),
                ],
            )

        if not statuses:
            body.append(html.Div(
                "Catalogue is empty for this ticker.",
                style={"color": PRJCT9_MUTED,
                       "fontSize": "11px"},
            ))
        else:
            body.extend(_row(s) for s in statuses)

        body.append(html.Div(
            id="catalogue-coverage-totals",
            style={"color": PRJCT9_MUTED,
                   "fontSize": "10px",
                   "letterSpacing": "1px",
                   "textTransform": "uppercase",
                   "marginTop": "8px"},
            children=(
                f"{int(totals.get('chart_ready') or 0)} ready / "
                f"{int(totals.get('saved_research_found') or 0)} "
                "to build / "
                f"{int(totals.get('no_saved_research') or 0)} missing"
            ),
        ))

        return _section_wrapper(
            "catalogue-coverage-summary",
            "CATALOGUE COVERAGE",
            f"what PRJCT9 knows about {target} right now",
            body,
        )

    def _render_best_patterns_section(results_data, meta):
        """Detail section: 'Patterns worth a look' table + a wider
        chart. Renders below the first-view summary so the user has
        room to inspect individual rows. The table's id stays as
        ``results-table`` so the existing selected-row-store callback
        keeps working.
        """
        body: list = []
        body.append(html.Div(
            "Each row tests one signal source against the ticker you "
            "are studying. Choose any row to see it explained.",
            style={"color": PRJCT9_TEXT,
                   "fontSize": "12px",
                   "lineHeight": "1.5",
                   "marginBottom": "8px"},
        ))
        if not results_data:
            body.append(html.Div(
                children=[
                    html.Div("No saved research found for this ticker yet.",
                             style={"fontSize": "13px",
                                    "marginBottom": "4px"}),
                    html.Div("Try SPY, QQQ, AAPL, or BTC-USD.",
                             style={"color": PRJCT9_MUTED,
                                    "fontSize": "12px"}),
                ],
                style={"padding": "10px 0"},
            ))
            return _section_wrapper(
                "best-patterns-section",
                "PATTERNS WORTH A LOOK",
                "every saved pattern for this ticker",
                body,
            )

        df = pd.DataFrame(results_data)

        # Interesting rows + auto-selected first row. The visible
        # columns are deliberately compact (no Avg daily move, no
        # P-value column, no internal source-mode field) so the table
        # does not need a horizontal scroll for the main useful info.
        # The selected-row card below the table still carries the full
        # per-row detail.
        interesting = _overview_interesting_rows(df, top_n=10)
        visible_columns = [
            "Primary Ticker", "Total Capture (%)", "Sharpe",
            "Trigger Days", "Significant 95%", "Evidence",
        ]
        interesting = interesting.reindex(
            columns=[c for c in visible_columns
                     if c in interesting.columns]
        )
        # Numeric formatting: Sharpe + Total Capture (%) stay numeric
        # (rounded to 2 decimals) so the DataTable can sort them
        # natively as numbers, not lexicographically as strings.
        # Trigger Days stays integer. Significant 95% becomes Yes/No.
        if "Total Capture (%)" in interesting.columns:
            interesting["Total Capture (%)"] = (
                pd.to_numeric(
                    interesting["Total Capture (%)"], errors="coerce",
                ).round(2)
            )
        if "Sharpe" in interesting.columns:
            interesting["Sharpe"] = (
                pd.to_numeric(
                    interesting["Sharpe"], errors="coerce",
                ).round(2)
            )
        if "Trigger Days" in interesting.columns:
            interesting["Trigger Days"] = (
                pd.to_numeric(
                    interesting["Trigger Days"], errors="coerce",
                ).astype("Int64")
            )
        if "Significant 95%" in interesting.columns:
            interesting["Significant 95%"] = (
                interesting["Significant 95%"].astype(str).map(
                    lambda v: (
                        "Yes" if str(v).strip().upper() == "YES"
                        else ("No" if str(v).strip().upper() == "NO"
                              else "-")
                    ),
                )
            )
        # Plain-language labels for the rendered column headers. The
        # underlying ``id`` keys stay as the source column names so the
        # row dicts carried by the selected-row store keep working.
        plain_header_map = {
            "Primary Ticker": "Signal source",
            "Total Capture (%)": "Total Capture (%)",
            "Sharpe": "Sharpe Ratio",
            "Trigger Days": "Signal days",
            "Significant 95%": "95% Confidence",
            "Evidence": "Evidence",
        }
        # Numeric column-type hints so Dash's native sort treats
        # Total Capture (%) and Sharpe Ratio as numbers, not strings.
        column_types = {
            "Total Capture (%)": "numeric",
            "Sharpe": "numeric",
            "Trigger Days": "numeric",
        }
        column_formats = {
            "Total Capture (%)": Format(precision=2, scheme=Scheme.fixed),
            "Sharpe": Format(precision=2, scheme=Scheme.fixed),
        }
        body.append(dash_table.DataTable(
            id="results-table",
            columns=[
                {
                    "name": plain_header_map.get(c, c),
                    "id": c,
                    "type": column_types.get(c, "text"),
                    **(
                        {"format": column_formats[c]}
                        if c in column_formats else {}
                    ),
                }
                for c in interesting.columns
            ],
            data=interesting.to_dict("records"),
            page_size=10,
            sort_action="native",
            sort_mode="single",
            sort_by=[{"column_id": "Sharpe", "direction": "desc"}],
            row_selectable="single",
            selected_rows=[0] if len(interesting) else [],
            style_table={"overflowX": "auto"},
            style_cell={
                "backgroundColor": PRJCT9_BLACK,
                "color": PRJCT9_TEXT,
                "fontFamily": "Consolas, 'Courier New', monospace",
                "fontSize": "12px",
                "padding": "5px 8px",
                "border": f"1px solid {PRJCT9_BORDER}",
            },
            style_header={
                "backgroundColor": PRJCT9_DIM,
                "color": PRJCT9_GREEN,
                "fontWeight": "bold",
                "border": f"1px solid {PRJCT9_GREEN}",
                "letterSpacing": "1px",
            },
            style_data_conditional=[
                {"if": {"row_index": "odd"},
                 "backgroundColor": PRJCT9_DIM},
                {"if": {"filter_query": '{Evidence} = "Strong historical sample"',
                        "column_id": "Evidence"},
                 "color": PRJCT9_GREEN, "fontWeight": "bold"},
                {"if": {"filter_query": '{Evidence} = "Too few signal days"',
                        "column_id": "Evidence"},
                 "color": PRJCT9_RED},
                {"if": {"filter_query": '{"Significant 95%"} = "Yes"',
                        "column_id": "Significant 95%"},
                 "color": PRJCT9_GREEN, "fontWeight": "bold"},
            ],
        ))

        # Larger chart at the bottom of the detail section so the table
        # is the primary content above and the chart adds context.
        body.append(html.Div(
            style={"marginTop": "10px"},
            children=[_overview_chart_top_n_capture(df, top_n=15,
                                                   compact=False)],
        ))

        return _section_wrapper(
            "best-patterns-section",
            "PATTERNS WORTH A LOOK",
            "every saved pattern for this ticker",
            body,
        )

    def _render_selected_pattern_section(results_data, meta, selected_row):
        """Selected Pattern panel: prioritizes the real cumulative
        capture chart for the (signal source, target) pair, with the
        compact key-values card above and a reconciliation line below
        comparing Final cumulative capture against the saved row's
        Total Capture (%). The studied-ticker price-history chart is
        intentionally NOT included here - it lives in its own detail
        section below the first view so the cumulative chart isn't
        clipped inside the first-view panel."""
        row = selected_row or _auto_select_best_row(results_data, meta)
        body = [
            # Primary chart first so the cumulative-capture surface
            # is fully visible above the fold. The signal-detail
            # key-values card and the reconciliation line follow.
            _render_cumulative_capture_chart(row, meta),
            _render_cumulative_capture_reconcile(row, meta),
            # Phase 6B-1: explicit one-row generator. Materializes a
            # canonical research_day_v1 artifact for the currently
            # selected (signal source, target) pair. Bounded, local,
            # offline. Clicks log Activity messages; the live chart
            # picks up the new artifact on next render.
            html.Button(
                "Build chart data for this pattern",
                id="btn-build-chart-data",
                n_clicks=0,
                style={**btn_style,
                       "marginTop": "6px",
                       "marginBottom": "0",
                       "width": "100%",
                       "boxSizing": "border-box",
                       "fontSize": "11px"},
            ),
            # The id="selected-pattern-body" is the target of the
            # _render_selected_pattern_only callback, which updates
            # only this subsection on row click.
            html.Div(
                id="selected-pattern-body",
                children=_render_selected_row_card(row, meta),
            ),
        ]
        return _section_wrapper(
            "selected-pattern-section",
            "SELECTED PATTERN",
            "cumulative capture for the strongest saved pattern - "
            "choose another from Patterns worth a look below",
            body,
        )

    def _render_cumulative_capture_reconcile(row, meta):
        """Plain reconciliation line under the cumulative-capture
        chart: shows Final cumulative capture and the saved row's
        Total Capture (%). When the two values differ materially
        (>1.0 percentage point), append a short note that the saved
        chart and table do not share the exact same date window."""
        if not isinstance(row, dict):
            return html.Div(style={"display": "none"})
        target = (
            row.get("Secondary Ticker") or
            (meta or {}).get("target") or DEFAULT_TARGET
        )
        signal_source = row.get("Primary Ticker")
        target = str(target).strip().upper() if target else ""
        signal_source = (
            str(signal_source).strip().upper()
            if signal_source else ""
        )
        try:
            row_total = float(row.get("Total Capture (%)"))
        except Exception:
            row_total = None
        if not signal_source or not target:
            return html.Div(style={"display": "none"})
        chart_final = None
        artifact_present = False
        # Phase 6B-1: prefer the saved artifact's rebuilt cumulative
        # for parity. Fall back to the live reconstruction.
        artifact = _read_research_day_artifact_for_pair(
            signal_source, target,
        )
        if artifact is not None and artifact.daily:
            artifact_present = True
            try:
                chart_final = float(
                    artifact.daily[-1].get("cumulative_capture_pct")
                )
            except Exception:
                chart_final = None
        if chart_final is None:
            df = _selected_pattern_cumulative_capture(
                signal_source, target,
            )
            if df is None or df.empty:
                return html.Div(style={"display": "none"})
            try:
                chart_final = float(df["cum_capture"].iloc[-1])
            except Exception:
                return html.Div(style={"display": "none"})
        line = (
            f"Final cumulative capture: {chart_final:.2f}%   "
            + (
                f"Selected row Total Capture (%): {row_total:.2f}"
                if row_total is not None else ""
            )
        )
        children = [html.Span(line)]
        if (
            row_total is not None
            and abs(chart_final - row_total) > 1.0
        ):
            # When the chart came from the saved artifact and still
            # disagrees with the saved row's Total Capture (%), the
            # mismatch is real (different metric grids). When the
            # chart came from the reconstruction fallback, the
            # mismatch is more likely a saved-date-window issue. The
            # message stays neutral.
            children.append(html.Span(
                "  Chart and table use different saved date windows.",
                style={"color": PRJCT9_MUTED,
                       "fontStyle": "italic"},
            ))
        return html.Div(
            id="cumulative-capture-reconcile",
            style={"color": PRJCT9_TEXT,
                   "fontSize": "10px",
                   "lineHeight": "1.5",
                   "marginTop": "6px",
                   "wordBreak": "normal"},
            children=children,
        )

    def _render_combined_signals_section(meta):
        """Compact panel showing saved combined-signal runs from disk.
        Filesystem-only; never invokes the engine live.

        For the cockpit grid this stays small: one short explanation,
        a 4-cell metric strip from the best run for the studied
        ticker, and a top-3 leaderboard mini-table. The full per-run
        list and the leaderboard chart that the older tab carried
        have been dropped to keep the panel inside its grid cell."""
        target = (meta.get("target") or DEFAULT_TARGET).strip().upper()
        runs = _discover_stack_runs(_stack_output_dir())
        my_runs = [r for r in runs if r["ticker"].upper() == target]
        other_runs = [r for r in runs if r["ticker"].upper() != target]
        ordered = my_runs + other_runs

        body: list = []
        body.append(html.Div(
            "Combined signals act only when several signals agree.",
            style={"color": PRJCT9_TEXT,
                   "fontSize": "12px",
                   "lineHeight": "1.5",
                   "marginBottom": "8px"},
        ))
        if not ordered:
            body.append(html.Div(
                "No saved combined-signal studies for this ticker yet.",
                style={"fontSize": "12px",
                       "color": PRJCT9_MUTED,
                       "lineHeight": "1.5"},
            ))
            return _section_wrapper(
                "combined-signals-detail",
                "COMBINED SIGNALS DETAIL",
                "stacks of agreeing signals",
                body,
            )

        first_run = ordered[0]
        first_summary = _load_stack_summary(first_run["run_path"])
        first_card = _stack_run_card(first_run, first_summary)

        def _fmt(v, fmt=".2f"):
            try:
                return format(float(v), fmt)
            except Exception:
                return "-"

        metric_cells = [
            ("Saved studies", str(len(ordered))),
            (
                "Best Sharpe Ratio",
                _fmt(first_card.get("best_risk_adjusted_score")),
            ),
            (
                "Best total move",
                f"{_fmt(first_card.get('best_total_move'))}%",
            ),
            (
                "Signal days at best",
                str(first_card.get("signal_days_at_best") or "-"),
            ),
        ]
        body.append(html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "repeat(2, minmax(0, 1fr))",
                "gap": "6px",
                "marginBottom": "8px",
            },
            children=[
                html.Div(
                    style={"backgroundColor": PRJCT9_DIM,
                           "border": f"1px solid {PRJCT9_BORDER}",
                           "padding": "6px 8px"},
                    children=[
                        html.Div(label,
                                 style={"color": PRJCT9_MUTED,
                                        "fontSize": "9px",
                                        "letterSpacing": "1px",
                                        "textTransform": "uppercase"}),
                        html.Div(value,
                                 style={"color": PRJCT9_GREEN,
                                        "fontSize": "12px",
                                        "fontWeight": "bold",
                                        "marginTop": "2px",
                                        "wordBreak": "break-word"}),
                    ],
                )
                for label, value in metric_cells
            ],
        ))

        # Plain explanation of K (rendered above any leaderboard).
        body.append(html.Div(
            "K means how many signals had to agree before the stack "
            "acted. Higher K is stricter: fewer days qualify, but the "
            "remaining signals may be stronger.",
            style={"color": PRJCT9_MUTED,
                   "fontSize": "11px",
                   "lineHeight": "1.5",
                   "marginBottom": "8px"},
        ))

        # Leaderboard table (table-first; numeric columns sortable).
        lb = _load_stack_leaderboard(first_run["run_path"])
        if lb is not None and not lb.empty:
            keep_cols = [c for c in (
                "K", "Trigger Days", "Total Capture (%)",
                "Sharpe Ratio", "Significant 95%",
            ) if c in lb.columns]
            lb_view = lb[keep_cols].head(10).copy()
            for nc in ("Total Capture (%)", "Sharpe Ratio"):
                if nc in lb_view.columns:
                    lb_view[nc] = (
                        pd.to_numeric(lb_view[nc], errors="coerce").round(2)
                    )
            for ic in ("K", "Trigger Days"):
                if ic in lb_view.columns:
                    lb_view[ic] = (
                        pd.to_numeric(lb_view[ic], errors="coerce")
                        .astype("Int64")
                    )
            rename_map = {
                "K": "Signals that must agree",
                "Trigger Days": "Signal days",
            }
            column_types_lb = {
                "K": "numeric",
                "Trigger Days": "numeric",
                "Total Capture (%)": "numeric",
                "Sharpe Ratio": "numeric",
            }
            body.append(html.Div(
                "Top combined stacks",
                style={"color": PRJCT9_MUTED,
                       "fontSize": "10px",
                       "letterSpacing": "1px",
                       "textTransform": "uppercase",
                       "marginBottom": "4px"},
            ))
            body.append(dash_table.DataTable(
                id="combined-signals-leaderboard",
                columns=[
                    {
                        "name": rename_map.get(c, c),
                        "id": c,
                        "type": column_types_lb.get(c, "text"),
                    }
                    for c in lb_view.columns
                ],
                data=lb_view.to_dict("records"),
                sort_action="native",
                sort_mode="single",
                sort_by=[
                    {"column_id": "Sharpe Ratio", "direction": "desc"}
                ],
                style_table={"overflowX": "auto"},
                style_cell={
                    "backgroundColor": PRJCT9_BLACK,
                    "color": PRJCT9_TEXT,
                    "fontFamily": "Consolas, 'Courier New', monospace",
                    "fontSize": "11px",
                    "padding": "4px 6px",
                    "border": f"1px solid {PRJCT9_BORDER}",
                },
                style_header={
                    "backgroundColor": PRJCT9_DIM,
                    "color": PRJCT9_GREEN,
                    "fontWeight": "bold",
                    "border": f"1px solid {PRJCT9_GREEN}",
                    "letterSpacing": "1px",
                    "fontSize": "10px",
                },
            ))

            # Diagnostic chart: stack strictness vs Total Capture (%).
            # Real numbers from the saved leaderboard. The chart is a
            # leaderboard diagnostic - the saved leaderboard does not
            # carry day-by-day stack history, so a true cumulative
            # stack capture chart is not available.
            try:
                import plotly.graph_objects as go
                if (
                    "K" in lb.columns
                    and "Total Capture (%)" in lb.columns
                ):
                    k_vals = pd.to_numeric(lb["K"], errors="coerce")
                    tot = pd.to_numeric(
                        lb["Total Capture (%)"], errors="coerce",
                    )
                    sharpe_vals = (
                        pd.to_numeric(lb["Sharpe Ratio"], errors="coerce")
                        if "Sharpe Ratio" in lb.columns
                        else pd.Series([None] * len(lb), index=lb.index)
                    )
                    trig_vals = (
                        pd.to_numeric(lb["Trigger Days"], errors="coerce")
                        if "Trigger Days" in lb.columns
                        else pd.Series([None] * len(lb), index=lb.index)
                    )
                    sig95_vals = (
                        lb["Significant 95%"].astype(str)
                        if "Significant 95%" in lb.columns
                        else pd.Series(["-"] * len(lb), index=lb.index)
                    )
                    mask = k_vals.notna() & tot.notna()
                    if mask.any():
                        hover = [
                            (
                                f"K = {int(k)} signals must agree<br>"
                                f"Signal days: "
                                f"{int(td) if pd.notna(td) else '-'}<br>"
                                f"Total Capture (%): {tt:.2f}<br>"
                                f"Sharpe Ratio: "
                                f"{(float(sh) if pd.notna(sh) else float('nan')):.2f}"
                                f"<br>95% Confidence: {s95}"
                            )
                            for k, td, tt, sh, s95 in zip(
                                k_vals[mask], trig_vals[mask],
                                tot[mask], sharpe_vals[mask],
                                sig95_vals[mask],
                            )
                        ]
                        fig = go.Figure(go.Scatter(
                            x=list(k_vals[mask]),
                            y=list(tot[mask]),
                            mode="markers+lines",
                            marker={"color": PRJCT9_GREEN, "size": 7},
                            line={"color": PRJCT9_BORDER, "width": 1},
                            hovertext=hover,
                            hoverinfo="text",
                        ))
                        fig.update_layout(
                            paper_bgcolor=PRJCT9_BLACK,
                            plot_bgcolor=PRJCT9_BLACK,
                            font={"color": PRJCT9_TEXT,
                                  "family": "Consolas, monospace",
                                  "size": 10},
                            xaxis={"gridcolor": PRJCT9_BORDER,
                                   "title": "Signals that must agree (K)"},
                            yaxis={"gridcolor": PRJCT9_BORDER,
                                   "title": "Total Capture (%)"},
                            margin={"l": 50, "r": 16,
                                    "t": 24, "b": 32},
                            height=200,
                            title={
                                "text": "Stack strictness vs Total Capture",
                                "font": {"color": PRJCT9_GREEN,
                                         "size": 11},
                            },
                        )
                        body.append(html.Div(
                            style={"marginTop": "10px"},
                            children=[
                                dcc.Graph(
                                    figure=fig,
                                    config={"displayModeBar": False},
                                ),
                            ],
                        ))
            except Exception:
                pass

        # Phase 6B-2: stack day-by-day artifact preference. When a
        # saved research_day_v1 stack artifact exists for the top
        # leaderboard row of the studied-ticker run, render the real
        # cumulative stack capture chart with an "exact saved stack
        # path" source line. Otherwise show an honest deferred note
        # plus a bounded one-row "Build stack chart data" button.
        run_id_for_target = first_run.get("run_dir") or first_run.get(
            "run_name",
        )
        top_K: Optional[int] = None
        if lb is not None and not lb.empty and "K" in lb.columns:
            try:
                top_K = int(
                    pd.to_numeric(lb["K"], errors="coerce").iloc[0]
                )
            except Exception:
                top_K = None
        stack_artifact = None
        if (
            run_id_for_target
            and top_K is not None
            and my_runs
        ):
            stack_artifact = _read_stack_artifact_for_run(
                target, run_id_for_target, top_K,
            )
        if stack_artifact is not None and stack_artifact.daily:
            body.append(_render_stack_cumulative_chart(stack_artifact))
        else:
            body.append(html.Div(
                "Stack chart data has not been built yet.",
                style={"color": PRJCT9_MUTED,
                       "fontSize": "11px",
                       "lineHeight": "1.5",
                       "marginTop": "8px"},
            ))
        # One-row build button. Only enabled for stacks belonging to
        # the studied ticker (otherwise the user would build artifacts
        # against unrelated runs).
        body.append(html.Button(
            "Build stack chart data",
            id="btn-build-stack-chart-data",
            n_clicks=0,
            style={**btn_style,
                   "marginTop": "8px",
                   "marginBottom": "0",
                   "width": "100%",
                   "boxSizing": "border-box",
                   "fontSize": "11px"},
        ))

        return _section_wrapper(
            "combined-signals-detail",
            "COMBINED SIGNALS DETAIL",
            "stacks of agreeing signals",
            body,
        )

    def _render_stack_cumulative_chart(artifact):
        """Render the saved stack day-by-day artifact's cumulative
        capture as a single Plotly line. Sourced exclusively from the
        saved artifact - never reconstructs.

        Surfaces a small reconciliation block: rebuilt final cumulative
        capture (from the daily rows) vs the saved leaderboard
        ``Total Capture (%)`` (when present in the artifact summary).
        Differences greater than 1 percentage point flag the saved
        date window mismatch plainly so the user does not assume the
        chart number must equal the leaderboard number.
        """
        try:
            import plotly.graph_objects as go
        except Exception:
            return html.Div(
                "Plotly unavailable; cannot draw stack chart.",
                style={"color": PRJCT9_MUTED, "fontSize": "11px"},
            )
        daily = artifact.daily or []
        dates = [r.get("date") for r in daily]
        cum = [r.get("cumulative_capture_pct") or 0.0 for r in daily]
        signals = [r.get("combined_signal") for r in daily]
        daily_caps = [r.get("daily_capture_pct") or 0.0 for r in daily]
        hover = [
            f"{d}<br>"
            f"Combined: {s}<br>"
            f"Daily Capture: {dc:.4f}%<br>"
            f"Cumulative Capture: {cc:.4f}%"
            for d, s, dc, cc in zip(dates, signals, daily_caps, cum)
        ]
        fig = go.Figure(go.Scatter(
            x=dates, y=cum, mode="lines",
            line={"color": PRJCT9_GREEN, "width": 1.4},
            hovertext=hover, hoverinfo="text",
        ))
        fig.add_hline(
            y=0.0, line_color=PRJCT9_BORDER, line_width=1,
        )
        member_label = ", ".join(artifact.members or [])[:80]
        fig.update_layout(
            paper_bgcolor=PRJCT9_BLACK,
            plot_bgcolor=PRJCT9_BLACK,
            font={"color": PRJCT9_TEXT, "family": "Consolas, monospace"},
            xaxis={"gridcolor": PRJCT9_BORDER, "title": "Date"},
            yaxis={"gridcolor": PRJCT9_BORDER,
                   "title": "Cumulative Capture (%)"},
            margin={"l": 56, "r": 12, "t": 28, "b": 36},
            height=220,
            title={
                "text": (
                    f"Stack Cumulative Capture - "
                    f"K={artifact.K} on {artifact.target_ticker}"
                    + (f" ({member_label})" if member_label else "")
                ),
                "font": {"color": PRJCT9_GREEN, "size": 12},
            },
        )

        # Reconciliation block: show the rebuilt final cumulative
        # capture from the saved daily rows alongside the saved
        # leaderboard's Total Capture (%) when one is recorded in the
        # artifact summary. Material mismatches (> 1 percentage point)
        # surface a plain note so the user does not chase the
        # discrepancy as a bug.
        summary = artifact.summary or {}
        final_cum = (
            float(cum[-1]) if cum and cum[-1] is not None else None
        )
        saved_total_raw = summary.get("total_capture_pct")
        try:
            saved_total = (
                float(saved_total_raw)
                if saved_total_raw is not None else None
            )
        except (TypeError, ValueError):
            saved_total = None
        rec_lines: list = []
        if final_cum is not None:
            rec_lines.append(
                f"Final Cumulative Capture (%): {final_cum:.2f}"
            )
        if saved_total is not None:
            rec_lines.append(
                f"Saved Total Capture (%): {saved_total:.2f}"
            )
        mismatch_note = None
        if (
            final_cum is not None and saved_total is not None
            and abs(final_cum - saved_total) > 1.0
        ):
            mismatch_note = (
                "Chart and leaderboard use different saved date windows."
            )
        recon_block_children: list = []
        if rec_lines:
            recon_block_children.append(html.Div(
                " | ".join(rec_lines),
                id="stack-reconciliation-line",
                style={"color": PRJCT9_TEXT,
                       "fontSize": "11px",
                       "letterSpacing": "0",
                       "marginTop": "4px",
                       "marginBottom": "2px"},
            ))
        if mismatch_note:
            recon_block_children.append(html.Div(
                mismatch_note,
                id="stack-reconciliation-mismatch",
                style={"color": PRJCT9_MUTED,
                       "fontSize": "10px",
                       "fontStyle": "italic",
                       "marginBottom": "4px"},
            ))

        return html.Div(
            id="stack-cumulative-capture-chart",
            style={"marginTop": "10px",
                   "border": f"1px solid {PRJCT9_BORDER}",
                   "padding": "6px 8px"},
            children=[
                html.Div(
                    "Chart data: exact saved stack path",
                    id="stack-chart-source",
                    style={"color": PRJCT9_GREEN,
                           "fontSize": "10px",
                           "letterSpacing": "1px",
                           "textTransform": "uppercase",
                           "marginBottom": "4px"},
                ),
                *recon_block_children,
                dcc.Graph(figure=fig, config={"displayModeBar": False}),
            ],
        )

    def _render_time_windows_section(meta):
        """Real 7-tier confluence status across timeframes for the
        studied ticker. Calls the production
        ``signal_library.confluence_analyzer`` engine
        (``load_confluence_data`` + ``align_signals_to_daily`` +
        ``calculate_confluence`` + ``calculate_time_in_signal``) on
        saved local libraries.

        Returns:
          - 7-tier label: Strong Buy / Buy / Weak Buy / Neutral /
            Weak Short / Short / Strong Short
          - alignment_pct + buy/short/none counts + alignment_since
          - per-timeframe current signal and time-in-signal
          - honest fallback when no saved libraries exist.

        Filesystem-only; the confluence_analyzer reads the same
        ``signal_library/data/stable/*.pkl`` files the preview helper
        uses. No network access."""
        target = (meta.get("target") or DEFAULT_TARGET).strip().upper()
        sig_dir = _signal_library_dir()

        body: list = []
        body.append(html.Div(
            "Multi-timeframe confluence status for this ticker. "
            "Powered by the real confluence engine on saved "
            "libraries.",
            style={"color": PRJCT9_TEXT,
                   "fontSize": "12px",
                   "lineHeight": "1.5",
                   "marginBottom": "8px"},
        ))

        snap = _real_confluence_snapshot_for_target(
            target, sig_lib_dir=sig_dir,
        )
        # Phase 6B-3 artifact + build-button block. Threaded through
        # ALL render branches (success, engine-unavailable, no-libs)
        # so the user can always see whether saved confluence chart
        # data exists and can always reach the build button.
        def _append_confluence_artifact_block(_target=target):
            confluence_artifact = _read_confluence_artifact_for_target(
                _target,
            )
            if (
                confluence_artifact is not None
                and confluence_artifact.daily
            ):
                body.append(_render_confluence_cumulative_chart(
                    confluence_artifact,
                ))
                body.append(_render_confluence_tier_distribution(
                    confluence_artifact,
                ))
            else:
                body.append(html.Div(
                    "Confluence chart data has not been built yet.",
                    style={"color": PRJCT9_MUTED,
                           "fontSize": "11px",
                           "lineHeight": "1.5",
                           "marginTop": "8px"},
                ))
            body.append(html.Button(
                "Build confluence chart data",
                id="btn-build-confluence-chart-data",
                n_clicks=0,
                style={**btn_style,
                       "marginTop": "8px",
                       "marginBottom": "0",
                       "width": "100%",
                       "boxSizing": "border-box",
                       "fontSize": "11px"},
            ))

        # Engine import / load failure - fall back to the simple
        # last-signal helper so the user still sees Buy / Short / None
        # rather than a blank section. Engine snapshot tier is the
        # primary surface; the simple helper is the fallback.
        if snap is None:
            simple_rows = _confluence_status_for_target(
                target, sig_lib_dir=sig_dir,
            )
            any_available = any(r.get("available") for r in simple_rows)
            if not simple_rows or not any_available:
                body.append(html.Div(
                    "No saved confluence libraries found for this "
                    "ticker yet.",
                    style={"fontSize": "12px",
                           "color": PRJCT9_MUTED,
                           "lineHeight": "1.5"},
                ))
                _append_confluence_artifact_block()
                return _section_wrapper(
                    "time-windows-detail",
                    "TIME WINDOWS DETAIL",
                    "7-tier confluence across timeframes",
                    body,
                )
            body.append(html.Div(
                "Real confluence engine unavailable; showing latest "
                "signal per timeframe only.",
                style={"color": PRJCT9_RED,
                       "fontSize": "11px",
                       "lineHeight": "1.5",
                       "marginBottom": "6px"},
            ))
            table_rows = []
            for r in simple_rows:
                if not r.get("available"):
                    table_rows.append({
                        "Timeframe": r["timeframe"],
                        "Current signal": "Library missing",
                        "Bars in signal": "-",
                        "Signal start": "-",
                    })
                    continue
                table_rows.append({
                    "Timeframe": r["timeframe"],
                    "Current signal": r.get("signal") or "-",
                    "Bars in signal":
                        r.get("bars_in_signal")
                        if r.get("bars_in_signal") is not None
                        else "-",
                    "Signal start":
                        r.get("signal_start_date") or "-",
                })
            body.append(dash_table.DataTable(
                id="time-windows-table",
                columns=[{"name": c, "id": c}
                         for c in ["Timeframe", "Current signal",
                                   "Bars in signal", "Signal start"]],
                data=table_rows,
                style_table={"overflowX": "auto"},
                style_cell={
                    "backgroundColor": PRJCT9_BLACK,
                    "color": PRJCT9_TEXT,
                    "fontFamily": "Consolas, 'Courier New', monospace",
                    "fontSize": "11px",
                    "padding": "5px 8px",
                    "border": f"1px solid {PRJCT9_BORDER}",
                    "textAlign": "left",
                },
                style_header={
                    "backgroundColor": PRJCT9_DIM,
                    "color": PRJCT9_GREEN,
                    "fontWeight": "bold",
                    "border": f"1px solid {PRJCT9_GREEN}",
                    "letterSpacing": "1px",
                    "fontSize": "10px",
                },
            ))
            _append_confluence_artifact_block()
            return _section_wrapper(
                "time-windows-detail",
                "TIME WINDOWS DETAIL",
                "7-tier confluence across timeframes",
                body,
            )

        # Real engine snapshot rendered.
        tier = snap.get("tier") or "Neutral"
        if "Buy" in tier:
            tier_color = PRJCT9_GREEN
        elif "Short" in tier:
            tier_color = PRJCT9_RED
        else:
            tier_color = PRJCT9_TEXT
        body.append(html.Div(
            f"Current confluence: {tier}",
            style={"color": tier_color,
                   "fontSize": "14px",
                   "fontWeight": "bold",
                   "letterSpacing": "1px",
                   "textTransform": "uppercase",
                   "marginBottom": "4px"},
        ))
        align_pct = float(snap.get("alignment_pct") or 0.0)
        body.append(html.Div(
            f"Alignment: {align_pct:.0f}%   "
            f"Buy {snap.get('buy_count', 0)} / "
            f"Short {snap.get('short_count', 0)} / "
            f"None {snap.get('none_count', 0)} "
            f"(active {snap.get('active_count', 0)} of "
            f"{snap.get('total_count', 0)})"
            + (
                f" - aligned since "
                f"{snap.get('alignment_since')}"
                if snap.get("alignment_since") else ""
            ),
            style={"color": PRJCT9_MUTED,
                   "fontSize": "11px",
                   "lineHeight": "1.5",
                   "marginBottom": "8px"},
        ))

        breakdown = snap.get("breakdown") or {}
        tis = snap.get("time_in_signal") or {}
        # Map the engine's interval keys to plain-language labels.
        interval_label = {
            "1d": "Daily",
            "1wk": "Weekly",
            "1mo": "Monthly",
            "3mo": "Quarterly",
            "1y": "Yearly",
        }
        ordered = ["1d", "1wk", "1mo", "3mo", "1y"]
        table_rows = []
        for iv in ordered:
            sig = breakdown.get(iv)
            tis_row = tis.get(iv) or {}
            if not sig:
                table_rows.append({
                    "Timeframe": interval_label.get(iv, iv),
                    "Current signal": "Library missing",
                    "Bars in signal": "-",
                    "Signal start": "-",
                })
                continue
            table_rows.append({
                "Timeframe": interval_label.get(iv, iv),
                "Current signal": sig,
                "Bars in signal": tis_row.get("bars") or "-",
                "Signal start": tis_row.get("entry_date_iso") or "-",
            })
        body.append(dash_table.DataTable(
            id="time-windows-table",
            columns=[
                {"name": c, "id": c}
                for c in ["Timeframe", "Current signal",
                          "Bars in signal", "Signal start"]
            ],
            data=table_rows,
            style_table={"overflowX": "auto"},
            style_cell={
                "backgroundColor": PRJCT9_BLACK,
                "color": PRJCT9_TEXT,
                "fontFamily": "Consolas, 'Courier New', monospace",
                "fontSize": "11px",
                "padding": "5px 8px",
                "border": f"1px solid {PRJCT9_BORDER}",
                "textAlign": "left",
            },
            style_header={
                "backgroundColor": PRJCT9_DIM,
                "color": PRJCT9_GREEN,
                "fontWeight": "bold",
                "border": f"1px solid {PRJCT9_GREEN}",
                "letterSpacing": "1px",
                "fontSize": "10px",
            },
            style_data_conditional=[
                {"if": {"filter_query": '{Current signal} = "Buy"',
                        "column_id": "Current signal"},
                 "color": PRJCT9_GREEN, "fontWeight": "bold"},
                {"if": {"filter_query": '{Current signal} = "Short"',
                        "column_id": "Current signal"},
                 "color": PRJCT9_RED, "fontWeight": "bold"},
                {"if": {"filter_query": '{Current signal} = "None"',
                        "column_id": "Current signal"},
                 "color": PRJCT9_MUTED},
                {"if": {"filter_query":
                            '{Current signal} = "Library missing"',
                        "column_id": "Current signal"},
                 "color": PRJCT9_MUTED, "fontStyle": "italic"},
            ],
        ))

        # Phase 6B-3: confluence day-by-day artifact preference. When a
        # saved research_day_v1 confluence artifact exists for the
        # studied ticker, render the real Confluence Capture Over Time
        # chart + tier distribution. Otherwise show an honest deferred
        # note plus a one-row "Build confluence chart data" button.
        _append_confluence_artifact_block()

        return _section_wrapper(
            "time-windows-detail",
            "TIME WINDOWS DETAIL",
            "Buy / Short / None status across timeframes",
            body,
        )

    def _render_confluence_cumulative_chart(artifact):
        """Render the saved confluence day-by-day artifact's
        cumulative capture as a Plotly line. Sourced exclusively from
        the saved artifact - never reconstructs."""
        try:
            import plotly.graph_objects as go
        except Exception:
            return html.Div(
                "Plotly unavailable; cannot draw confluence chart.",
                style={"color": PRJCT9_MUTED, "fontSize": "11px"},
            )
        daily = artifact.daily or []
        dates = [r.get("date") for r in daily]
        cum = [r.get("cumulative_capture_pct") or 0.0 for r in daily]
        signals = [r.get("confluence_signal") for r in daily]
        tiers = [r.get("confluence_tier") for r in daily]
        daily_caps = [r.get("daily_capture_pct") or 0.0 for r in daily]
        tf_snaps = [r.get("timeframe_signals") or {} for r in daily]
        hover = []
        for d, t, sig, tf, dc, cc in zip(
            dates, tiers, signals, tf_snaps, daily_caps, cum,
        ):
            tf_text = " / ".join(
                f"{k}: {v}" for k, v in tf.items()
            )
            hover.append(
                f"{d}<br>"
                f"Tier: {t}<br>"
                f"Confluence: {sig}<br>"
                f"Timeframes: {tf_text}<br>"
                f"Daily Capture: {dc:.4f}%<br>"
                f"Cumulative Capture: {cc:.4f}%"
            )
        fig = go.Figure(go.Scatter(
            x=dates, y=cum, mode="lines",
            line={"color": PRJCT9_GREEN, "width": 1.4},
            hovertext=hover, hoverinfo="text",
        ))
        fig.add_hline(
            y=0.0, line_color=PRJCT9_BORDER, line_width=1,
        )
        fig.update_layout(
            paper_bgcolor=PRJCT9_BLACK,
            plot_bgcolor=PRJCT9_BLACK,
            font={"color": PRJCT9_TEXT, "family": "Consolas, monospace"},
            xaxis={"gridcolor": PRJCT9_BORDER, "title": "Date"},
            yaxis={"gridcolor": PRJCT9_BORDER,
                   "title": "Cumulative Capture (%)"},
            margin={"l": 56, "r": 12, "t": 28, "b": 36},
            height=220,
            title={
                "text": "Confluence Capture Over Time",
                "font": {"color": PRJCT9_GREEN, "size": 12},
            },
        )
        summary = artifact.summary or {}
        final_cum = (
            float(cum[-1]) if cum and cum[-1] is not None else None
        )
        rec_lines: list = []
        if final_cum is not None:
            rec_lines.append(
                f"Final Cumulative Capture (%): {final_cum:.2f}"
            )
        trig = summary.get("rebuilt_trigger_days")
        if trig is not None:
            rec_lines.append(f"Signal days: {int(trig)}")
        return html.Div(
            id="confluence-cumulative-capture-chart",
            style={"marginTop": "10px",
                   "border": f"1px solid {PRJCT9_BORDER}",
                   "padding": "6px 8px"},
            children=[
                html.Div(
                    "Chart data: exact saved confluence path",
                    id="confluence-chart-source",
                    style={"color": PRJCT9_GREEN,
                           "fontSize": "10px",
                           "letterSpacing": "1px",
                           "textTransform": "uppercase",
                           "marginBottom": "4px"},
                ),
                html.Div(
                    " | ".join(rec_lines),
                    id="confluence-reconciliation-line",
                    style={"color": PRJCT9_TEXT,
                           "fontSize": "11px",
                           "marginTop": "2px",
                           "marginBottom": "4px"},
                ) if rec_lines else html.Div(),
                dcc.Graph(figure=fig, config={"displayModeBar": False}),
            ],
        )

    def _render_confluence_tier_distribution(artifact):
        """Render a compact 7-tier count summary for the saved
        confluence artifact. One row per tier, plain count."""
        summary = artifact.summary or {}
        tier_counts = summary.get("tier_counts") or {}
        order = [
            ("Strong Buy", "strong_buy"),
            ("Buy", "buy"),
            ("Weak Buy", "weak_buy"),
            ("Neutral", "neutral"),
            ("Weak Short", "weak_short"),
            ("Short", "short"),
            ("Strong Short", "strong_short"),
        ]
        rows = []
        for label, key in order:
            cnt = tier_counts.get(key)
            try:
                cnt_str = str(int(cnt)) if cnt is not None else "0"
            except (TypeError, ValueError):
                cnt_str = "0"
            rows.append({"Tier": label, "Days": cnt_str})
        return html.Div(
            id="confluence-tier-distribution",
            style={"marginTop": "8px"},
            children=[
                html.Div(
                    "TIER DISTRIBUTION",
                    style={"color": PRJCT9_MUTED,
                           "fontSize": "10px",
                           "letterSpacing": "1px",
                           "marginBottom": "4px"},
                ),
                dash_table.DataTable(
                    columns=[
                        {"name": "Tier", "id": "Tier"},
                        {"name": "Days", "id": "Days"},
                    ],
                    data=rows,
                    style_table={"overflowX": "auto"},
                    style_cell={
                        "backgroundColor": PRJCT9_BLACK,
                        "color": PRJCT9_TEXT,
                        "fontFamily": "Consolas, 'Courier New', monospace",
                        "fontSize": "11px",
                        "padding": "4px 8px",
                        "border": f"1px solid {PRJCT9_BORDER}",
                        "textAlign": "left",
                    },
                    style_header={
                        "backgroundColor": PRJCT9_DIM,
                        "color": PRJCT9_GREEN,
                        "fontWeight": "bold",
                        "border": f"1px solid {PRJCT9_GREEN}",
                        "letterSpacing": "1px",
                        "fontSize": "10px",
                    },
                ),
            ],
        )

    def _render_signal_rules_section():
        """Signal Rules: one plain sentence describing how signals are
        built, plus a short follow-up. Sentence form per the cockpit
        spec."""
        s = _signal_engine_settings()
        body = [
            html.Div(
                f"Signals are built from moving-average windows up to "
                f"{s['max_sma_day']} trading days, using daily Close prices.",
                style={"color": PRJCT9_TEXT,
                       "fontSize": "12px",
                       "lineHeight": "1.5",
                       "marginBottom": "6px"},
            ),
            html.Div(
                "Each pattern compares one ticker's signal to this "
                "ticker's next-day move.",
                style={"color": PRJCT9_MUTED,
                       "fontSize": "11px",
                       "lineHeight": "1.5"},
            ),
        ]
        return _section_wrapper(
            "signal-rules-section",
            "SIGNAL RULES DETAIL",
            "how signals are built",
            body,
        )

    def _render_activity_section(log):
        """Compact Activity panel showing the latest 4-6 plain-language
        log lines, most recent first."""
        if not log:
            body = [html.Div(
                "Nothing yet. Open a saved ticker study or test 10 "
                "signal sources.",
                style={"color": PRJCT9_MUTED,
                       "fontSize": "11px",
                       "padding": "4px 0"},
            )]
        else:
            recent = list(log)[-6:][::-1]
            body = [html.Pre(
                "\n".join(recent),
                style={
                    "color": PRJCT9_TEXT,
                    "backgroundColor": PRJCT9_BLACK,
                    "padding": "6px 8px",
                    "border": f"1px solid {PRJCT9_BORDER}",
                    "fontSize": "10px",
                    "whiteSpace": "pre-wrap",
                    "maxHeight": "140px",
                    "overflow": "auto",
                    "margin": "0",
                    "lineHeight": "1.5",
                },
            )]
        return _section_wrapper(
            "activity-section",
            "ACTIVITY DETAIL",
            "what just happened",
            body,
        )

    def _render_research_cockpit(
        results_data, meta, log, selected_row, catalogue_summary=None,
    ):
        """Compose the single-screen research cockpit.

        First viewport (desktop 1365x768 / mobile 390x844):
          - Title + one-sentence engine explainer
          - 'At a glance' grid (Patterns / Combined signals / Time
            windows / Signal rules)
          - 3-column first-view summary row:
              Best Pattern Summary | Selected Pattern | Catalogue
              Coverage

        Below the first viewport, detail sections stack naturally and
        scroll with the page:
          - Patterns worth a look (full table + chart)
          - Combined Signals detail
          - Time Windows detail
          - Signal Rules detail
          - Activity detail
        """
        target = (meta.get("target") or DEFAULT_TARGET).strip().upper()
        return html.Div(children=[
            _render_dashboard_header(target, results_data, meta),
            html.Div(
                className="prjct9-cockpit-grid",
                children=[
                    html.Div(
                        className="prjct9-firstview-row",
                        children=[
                            _render_best_pattern_summary(
                                results_data, meta,
                            ),
                            _render_selected_pattern_section(
                                results_data, meta, selected_row,
                            ),
                            _render_catalogue_coverage(
                                meta, catalogue_summary,
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(
                className="prjct9-detail-stack",
                children=[
                    _render_market_scan_section(meta),
                    _render_best_patterns_section(results_data, meta),
                    _render_price_history_detail_section(
                        results_data, meta, selected_row,
                    ),
                    _render_combined_signals_section(meta),
                    _render_time_windows_section(meta),
                    _render_traffic_flow_section(meta),
                    _render_signal_rules_section(),
                    _render_activity_section(log),
                ],
            ),
        ])

    def _render_price_history_detail_section(
        results_data, meta, selected_row,
    ):
        """Detail section that renders the studied-ticker price chart
        full-width, below the first-view summary. Moved here so the
        Selected Pattern panel in the first view can give the
        cumulative-capture chart the room it needs."""
        row = selected_row or _auto_select_best_row(results_data, meta)
        target = (meta.get("target") or DEFAULT_TARGET).strip().upper()
        body = [
            html.Div(
                f"{target} price history from saved local data. "
                "Context for the cumulative-capture chart above.",
                style={"color": PRJCT9_TEXT,
                       "fontSize": "12px",
                       "lineHeight": "1.5",
                       "marginBottom": "8px"},
            ),
            _render_local_price_chart(row, meta),
        ]
        return _section_wrapper(
            "price-history-detail",
            "PRICE HISTORY",
            "studied-ticker close price over time",
            body,
        )

    def _render_traffic_flow_section(meta):
        """Traffic Flow detail section.

        Phase 6B-4: when a saved TrafficFlow day-by-day pressure
        artifact exists for the studied ticker's top stack run, the
        section renders a real Pressure Over Time chart + a compact
        pressure distribution summary, sourced exclusively from the
        saved JSON. When no artifact exists, the section falls back
        to the read-only snapshot built from each member's latest
        cached signal (preserving the prior cockpit behavior). The
        bounded "Build traffic flow chart data" button materializes
        the artifact for the studied ticker.

        Real read-only snapshot: parse the top stack's members from
        the saved StackBuilder leaderboard, read each member's latest
        Spymaster signal, count Buy / Short / None, render the member
        breakdown table + a counts bar chart. Uses
        ``trafficflow.parse_members_with_protocol`` +
        ``_next_signal_from_pkl`` + ``_calculate_signal_mix`` (no
        ``_signal_snapshot_for_members`` so no yfinance fallback)."""
        target = (meta.get("target") or DEFAULT_TARGET).strip().upper()
        body: list = []
        body.append(html.Div(
            "Traffic Flow looks at combined signal pressure across "
            "saved stack members and the ticker being studied.",
            style={"color": PRJCT9_TEXT,
                   "fontSize": "12px",
                   "lineHeight": "1.5",
                   "marginBottom": "8px"},
        ))

        # Phase 6B-4 artifact + build-button block. Threaded through
        # both render branches (snapshot success, no-runs fallback)
        # so the user can always see whether saved TrafficFlow chart
        # data exists and can always reach the build button.
        runs = _discover_stack_runs(_stack_output_dir())
        target_runs = [
            r for r in runs if r["ticker"].upper() == target
        ]
        run_id_for_artifact = (
            (
                target_runs[0].get("run_dir")
                or target_runs[0].get("run_name")
            )
            if target_runs else None
        )

        def _append_trafficflow_artifact_block():
            traffic_artifact = (
                _read_trafficflow_artifact_for_run(
                    target, run_id_for_artifact,
                )
                if run_id_for_artifact else None
            )
            if (
                traffic_artifact is not None
                and traffic_artifact.daily
            ):
                body.append(
                    _render_trafficflow_pressure_chart(
                        traffic_artifact,
                    ),
                )
                body.append(
                    _render_trafficflow_pressure_distribution(
                        traffic_artifact,
                    ),
                )
            else:
                body.append(html.Div(
                    "Traffic flow chart data has not been built yet.",
                    style={"color": PRJCT9_MUTED,
                           "fontSize": "11px",
                           "lineHeight": "1.5",
                           "marginTop": "8px"},
                ))
            body.append(html.Button(
                "Build traffic flow chart data",
                id="btn-build-trafficflow-chart-data",
                n_clicks=0,
                style={**btn_style,
                       "marginTop": "8px",
                       "marginBottom": "0",
                       "width": "100%",
                       "boxSizing": "border-box",
                       "fontSize": "11px"},
            ))

        snap = _traffic_flow_snapshot_for_target(target)
        if snap is None or not snap.get("members"):
            body.append(html.Div(
                f"No saved stack runs found for {target}. Traffic "
                "Flow needs saved stack members; the read-only "
                "snapshot is not available yet.",
                style={"color": PRJCT9_MUTED,
                       "fontSize": "11px",
                       "lineHeight": "1.5"},
            ))
            _append_trafficflow_artifact_block()
            return _section_wrapper(
                "traffic-flow-detail",
                "TRAFFIC FLOW",
                "combined signal pressure across stack members",
                body,
            )

        pressure = snap.get("pressure") or "None"
        if pressure == "Buy pressure":
            pressure_color = PRJCT9_GREEN
        elif pressure == "Short pressure":
            pressure_color = PRJCT9_RED
        elif pressure == "Mixed":
            pressure_color = PRJCT9_TEXT
        else:
            pressure_color = PRJCT9_MUTED
        body.append(html.Div(
            f"Current pressure: {pressure}",
            style={"color": pressure_color,
                   "fontSize": "13px",
                   "fontWeight": "bold",
                   "letterSpacing": "1px",
                   "textTransform": "uppercase",
                   "marginBottom": "4px"},
        ))
        body.append(html.Div(
            f"Members tested: {len(snap['members'])} "
            f"(K={snap.get('top_k') if snap.get('top_k') is not None else '-'}). "
            f"Buy {snap.get('buy_count', 0)} / "
            f"Short {snap.get('short_count', 0)} / "
            f"None {snap.get('none_count', 0)}"
            + (
                f" / Missing {snap.get('missing_count', 0)}"
                if snap.get("missing_count") else ""
            )
            + (
                f". Protocol match: {snap.get('protocol_mix')}."
                if snap.get("protocol_mix") else "."
            ),
            style={"color": PRJCT9_MUTED,
                   "fontSize": "11px",
                   "lineHeight": "1.5",
                   "marginBottom": "8px"},
        ))

        # Members breakdown table.
        member_rows = []
        for m in snap["members"]:
            member_rows.append({
                "Member": m["ticker"],
                "Protocol": (
                    "Direct" if m["protocol"] == "D"
                    else "Inverse" if m["protocol"] == "I"
                    else "-"
                ),
                "Current signal": m["signal"],
            })
        body.append(dash_table.DataTable(
            id="traffic-flow-members-table",
            columns=[{"name": c, "id": c}
                     for c in ["Member", "Protocol", "Current signal"]],
            data=member_rows,
            style_table={"overflowX": "auto"},
            style_cell={
                "backgroundColor": PRJCT9_BLACK,
                "color": PRJCT9_TEXT,
                "fontFamily": "Consolas, 'Courier New', monospace",
                "fontSize": "11px",
                "padding": "5px 8px",
                "border": f"1px solid {PRJCT9_BORDER}",
                "textAlign": "left",
            },
            style_header={
                "backgroundColor": PRJCT9_DIM,
                "color": PRJCT9_GREEN,
                "fontWeight": "bold",
                "border": f"1px solid {PRJCT9_GREEN}",
                "letterSpacing": "1px",
                "fontSize": "10px",
            },
            style_data_conditional=[
                {"if": {"filter_query": '{Current signal} = "Buy"',
                        "column_id": "Current signal"},
                 "color": PRJCT9_GREEN, "fontWeight": "bold"},
                {"if": {"filter_query": '{Current signal} = "Short"',
                        "column_id": "Current signal"},
                 "color": PRJCT9_RED, "fontWeight": "bold"},
                {"if": {"filter_query": '{Current signal} = "None"',
                        "column_id": "Current signal"},
                 "color": PRJCT9_MUTED},
                {"if": {"filter_query":
                            '{Current signal} = "missing"',
                        "column_id": "Current signal"},
                 "color": PRJCT9_MUTED, "fontStyle": "italic"},
            ],
        ))

        # Counts bar chart.
        try:
            import plotly.graph_objects as go
            counts = [
                ("Buy", snap.get("buy_count", 0), PRJCT9_GREEN),
                ("Short", snap.get("short_count", 0), PRJCT9_RED),
                ("None", snap.get("none_count", 0), PRJCT9_MUTED),
            ]
            if snap.get("missing_count"):
                counts.append(
                    ("Missing cache",
                     snap.get("missing_count", 0),
                     PRJCT9_BORDER),
                )
            fig = go.Figure(go.Bar(
                x=[c[1] for c in counts],
                y=[c[0] for c in counts],
                orientation="h",
                marker={"color": [c[2] for c in counts]},
            ))
            fig.update_layout(
                paper_bgcolor=PRJCT9_BLACK,
                plot_bgcolor=PRJCT9_BLACK,
                font={"color": PRJCT9_TEXT,
                      "family": "Consolas, monospace", "size": 10},
                xaxis={"gridcolor": PRJCT9_BORDER,
                       "title": "Member count"},
                yaxis={"gridcolor": PRJCT9_BORDER, "title": "",
                       "automargin": True},
                margin={"l": 80, "r": 16, "t": 24, "b": 32},
                height=180,
                title={
                    "text": "Member signal counts",
                    "font": {"color": PRJCT9_GREEN, "size": 11},
                },
            )
            body.append(html.Div(
                style={"marginTop": "10px"},
                children=[
                    dcc.Graph(figure=fig,
                              config={"displayModeBar": False}),
                ],
            ))
        except Exception:
            pass

        body.append(html.Div(
            "Read-only snapshot. Run TrafficFlow as a standalone "
            "Dash app for the full per-day pressure surface.",
            style={"color": PRJCT9_MUTED,
                   "fontSize": "10px",
                   "lineHeight": "1.5",
                   "marginTop": "8px"},
        ))
        _append_trafficflow_artifact_block()
        return _section_wrapper(
            "traffic-flow-detail",
            "TRAFFIC FLOW",
            "combined signal pressure across stack members",
            body,
        )

    def _render_trafficflow_pressure_chart(artifact):
        """Render the saved TrafficFlow day-by-day artifact's
        cumulative pressure-capture as a single Plotly line.
        Sourced exclusively from the saved artifact -- never
        reconstructs."""
        try:
            import plotly.graph_objects as go
        except Exception:
            return html.Div(
                "Plotly unavailable; cannot draw traffic flow chart.",
                style={"color": PRJCT9_MUTED, "fontSize": "11px"},
            )
        daily = artifact.daily or []
        dates = [r.get("date") for r in daily]
        cum = [r.get("cumulative_capture_pct") or 0.0 for r in daily]
        signals = [r.get("pressure_signal") for r in daily]
        daily_caps = [r.get("daily_capture_pct") or 0.0 for r in daily]
        member_signals = [
            r.get("member_signals") or {} for r in daily
        ]
        hover = []
        for d, sig, ms, dc, cc in zip(
            dates, signals, member_signals, daily_caps, cum,
        ):
            members_text = " / ".join(
                f"{k}: {v}" for k, v in ms.items()
            )
            hover.append(
                f"{d}<br>"
                f"Pressure: {sig}<br>"
                f"Members: {members_text}<br>"
                f"Daily Capture: {dc:.4f}%<br>"
                f"Cumulative Capture: {cc:.4f}%"
            )
        fig = go.Figure(go.Scatter(
            x=dates, y=cum, mode="lines",
            line={"color": PRJCT9_GREEN, "width": 1.4},
            hovertext=hover, hoverinfo="text",
        ))
        fig.add_hline(
            y=0.0, line_color=PRJCT9_BORDER, line_width=1,
        )
        member_label = ", ".join(artifact.members or [])[:80]
        fig.update_layout(
            paper_bgcolor=PRJCT9_BLACK,
            plot_bgcolor=PRJCT9_BLACK,
            font={"color": PRJCT9_TEXT,
                  "family": "Consolas, monospace"},
            xaxis={"gridcolor": PRJCT9_BORDER, "title": "Date"},
            yaxis={"gridcolor": PRJCT9_BORDER,
                   "title": "Cumulative Capture (%)"},
            margin={"l": 56, "r": 12, "t": 28, "b": 36},
            height=220,
            title={
                "text": (
                    "Traffic Flow Pressure Over Time"
                    + (
                        f" - {artifact.target_ticker}"
                        f" ({member_label})"
                        if member_label else
                        f" - {artifact.target_ticker}"
                    )
                ),
                "font": {"color": PRJCT9_GREEN, "size": 12},
            },
        )
        summary = artifact.summary or {}
        final_cum = (
            float(cum[-1]) if cum and cum[-1] is not None else None
        )
        rec_lines: list = []
        if final_cum is not None:
            rec_lines.append(
                f"Final Cumulative Capture (%): {final_cum:.2f}"
            )
        trig = summary.get("rebuilt_trigger_days")
        if trig is not None:
            rec_lines.append(f"Signal-day capture: {int(trig)}")
        return html.Div(
            id="trafficflow-pressure-chart",
            style={"marginTop": "10px",
                   "border": f"1px solid {PRJCT9_BORDER}",
                   "padding": "6px 8px"},
            children=[
                html.Div(
                    "Chart data: exact saved traffic flow path",
                    id="trafficflow-chart-source",
                    style={"color": PRJCT9_GREEN,
                           "fontSize": "10px",
                           "letterSpacing": "1px",
                           "textTransform": "uppercase",
                           "marginBottom": "4px"},
                ),
                html.Div(
                    " | ".join(rec_lines),
                    id="trafficflow-reconciliation-line",
                    style={"color": PRJCT9_TEXT,
                           "fontSize": "11px",
                           "marginTop": "2px",
                           "marginBottom": "4px"},
                ) if rec_lines else html.Div(),
                html.Div(
                    "Signal-day capture, not portfolio return: "
                    "the cumulative line sums what the pressure "
                    "signal would have captured each trigger day, "
                    "without trade-cost or position-sizing "
                    "assumptions.",
                    style={"color": PRJCT9_MUTED,
                           "fontSize": "10px",
                           "fontStyle": "italic",
                           "marginBottom": "4px"},
                ),
                dcc.Graph(figure=fig, config={"displayModeBar": False}),
            ],
        )

    def _render_trafficflow_pressure_distribution(artifact):
        """Render a compact pressure-count summary for the saved
        TrafficFlow artifact: one row per pressure value (Buy /
        Short / None), with the day-count from
        ``summary.pressure_counts``."""
        summary = artifact.summary or {}
        counts = summary.get("pressure_counts") or {}
        order = [
            ("Buy pressure", "buy"),
            ("Short pressure", "short"),
            ("None / mixed", "none"),
        ]
        rows = []
        for label, key in order:
            cnt = counts.get(key)
            try:
                cnt_str = str(int(cnt)) if cnt is not None else "0"
            except (TypeError, ValueError):
                cnt_str = "0"
            rows.append({"Pressure": label, "Days": cnt_str})
        return html.Div(
            id="trafficflow-pressure-distribution",
            style={"marginTop": "8px"},
            children=[
                html.Div(
                    "PRESSURE DISTRIBUTION",
                    style={"color": PRJCT9_MUTED,
                           "fontSize": "10px",
                           "letterSpacing": "1px",
                           "marginBottom": "4px"},
                ),
                dash_table.DataTable(
                    columns=[
                        {"name": "Pressure", "id": "Pressure"},
                        {"name": "Days", "id": "Days"},
                    ],
                    data=rows,
                    style_table={"overflowX": "auto"},
                    style_cell={
                        "backgroundColor": PRJCT9_BLACK,
                        "color": PRJCT9_TEXT,
                        "fontFamily": "Consolas, 'Courier New', monospace",
                        "fontSize": "11px",
                        "padding": "4px 8px",
                        "border": f"1px solid {PRJCT9_BORDER}",
                        "textAlign": "left",
                    },
                    style_header={
                        "backgroundColor": PRJCT9_DIM,
                        "color": PRJCT9_GREEN,
                        "fontWeight": "bold",
                        "border": f"1px solid {PRJCT9_GREEN}",
                        "letterSpacing": "1px",
                        "fontSize": "10px",
                    },
                ),
            ],
        )

    def _render_market_scan_section(meta):
        """Detail section: saved OnePass output rendered as a compact
        Market Scan summary. Filesystem-only; no live full-universe
        run is triggered from this section."""
        meta = meta or {}
        path_str = meta.get("market_scan_path")
        body: list = []
        body.append(html.Div(
            "Market Scan looks across many tickers first, before "
            "you study one ticker in detail.",
            style={"color": PRJCT9_TEXT,
                   "fontSize": "12px",
                   "lineHeight": "1.5",
                   "marginBottom": "8px"},
        ))
        if not path_str:
            body.append(html.Div(
                "No saved market scan found yet.",
                style={"color": PRJCT9_MUTED,
                       "fontSize": "12px",
                       "lineHeight": "1.5"},
            ))
            return _section_wrapper(
                "market-scan-section",
                "MARKET SCAN",
                "OnePass outliers across the ticker universe",
                body,
            )
        try:
            scan = _load_onepass_summary(Path(path_str), top_n=10)
        except Exception:
            scan = None
        if not scan:
            body.append(html.Div(
                "Saved market scan could not be read.",
                style={"color": PRJCT9_MUTED,
                       "fontSize": "12px"},
            ))
            return _section_wrapper(
                "market-scan-section",
                "MARKET SCAN",
                "OnePass outliers across the ticker universe",
                body,
            )

        body.append(html.Div(
            f"{int(scan.get('rows') or 0):,} tickers scanned. "
            "Top outliers below.",
            style={"color": PRJCT9_GREEN,
                   "fontSize": "11px",
                   "letterSpacing": "1px",
                   "textTransform": "uppercase",
                   "marginBottom": "6px"},
        ))

        top_df = scan.get("top")
        if top_df is not None and not top_df.empty:
            display = top_df.copy()
            for col, fmt in (
                ("Total Capture (%)", lambda v: f"{float(v):.2f}"),
                ("Sharpe Ratio", lambda v: f"{float(v):.2f}"),
            ):
                if col in display.columns:
                    display[col] = display[col].apply(
                        lambda v, f=fmt: (
                            f(v) if pd.notna(v) else "-"
                        ),
                    )
            rename = {
                "Primary Ticker": "Ticker",
                "Trigger Days": "Signal days",
                "Significant 95%": "95% Confidence",
            }
            display = display.rename(columns=rename)
            body.append(dash_table.DataTable(
                columns=[{"name": c, "id": c} for c in display.columns],
                data=display.to_dict("records"),
                style_table={"overflowX": "auto"},
                style_cell={
                    "backgroundColor": PRJCT9_BLACK,
                    "color": PRJCT9_TEXT,
                    "fontFamily": "Consolas, 'Courier New', monospace",
                    "fontSize": "11px",
                    "padding": "4px 6px",
                    "border": f"1px solid {PRJCT9_BORDER}",
                },
                style_header={
                    "backgroundColor": PRJCT9_DIM,
                    "color": PRJCT9_GREEN,
                    "fontWeight": "bold",
                    "border": f"1px solid {PRJCT9_GREEN}",
                    "letterSpacing": "1px",
                    "fontSize": "10px",
                },
            ))
            # Compact horizontal-bar chart of top outliers by Total move.
            if "Total move" in display.columns and len(display) > 0:
                try:
                    import plotly.graph_objects as go
                    nums = pd.to_numeric(
                        display["Total move"], errors="coerce",
                    ).dropna()
                    labels = (
                        display.loc[nums.index, "Ticker"].astype(str)
                        if "Ticker" in display.columns
                        else pd.Series([str(i) for i in nums.index])
                    )
                    if not nums.empty:
                        fig = go.Figure(go.Bar(
                            x=list(nums)[::-1],
                            y=list(labels)[::-1],
                            orientation="h",
                            marker={"color": PRJCT9_GREEN},
                        ))
                        fig.update_layout(
                            paper_bgcolor=PRJCT9_BLACK,
                            plot_bgcolor=PRJCT9_BLACK,
                            font={"color": PRJCT9_TEXT,
                                  "family": "Consolas, monospace",
                                  "size": 10},
                            xaxis={"gridcolor": PRJCT9_BORDER,
                                   "title": "Total move (%)"},
                            yaxis={"gridcolor": PRJCT9_BORDER,
                                   "title": "", "automargin": True},
                            margin={"l": 70, "r": 16, "t": 24, "b": 32},
                            height=240,
                            title={"text": "Top market-scan outliers",
                                   "font": {"color": PRJCT9_GREEN,
                                            "size": 12}},
                        )
                        body.append(html.Div(
                            style={"marginTop": "10px"},
                            children=[
                                dcc.Graph(
                                    figure=fig,
                                    config={"displayModeBar": False},
                                ),
                            ],
                        ))
                except Exception:
                    pass
        return _section_wrapper(
            "market-scan-section",
            "MARKET SCAN",
            "OnePass outliers across the ticker universe",
            body,
        )

    # ----------------------------------------------------------------- overview

    def _overview_chart_top_n_capture(df, top_n=8, compact=True):
        """Horizontal bar chart: top N signal sources by Total Capture
        (%). Compact mode (default) renders short with no x-axis title
        and a tight margin so it fits inside the first-view Best
        Pattern Summary panel. compact=False renders a taller version
        suitable for the detail section."""
        try:
            import plotly.graph_objects as go
        except Exception:
            return html.Div("plotly unavailable", style={"color": PRJCT9_MUTED})
        if df is None or df.empty or "Total Capture (%)" not in df.columns:
            return html.Div("Not enough data to chart.",
                            style={"color": PRJCT9_MUTED, "padding": "10px"})
        d = df.copy()
        d["Total Capture (%)"] = pd.to_numeric(
            d["Total Capture (%)"], errors="coerce",
        )
        d = d.dropna(subset=["Total Capture (%)"]).sort_values(
            "Total Capture (%)", ascending=False,
        ).head(int(top_n))
        if d.empty:
            return html.Div("Not enough finite move data to chart.",
                            style={"color": PRJCT9_MUTED, "padding": "10px"})
        labels = (
            d["Primary Ticker"].astype(str)
            if "Primary Ticker" in d.columns
            else pd.Series([str(i) for i in range(len(d))])
        )
        # Horizontal bars keep the labels readable even when many short
        # tickers crowd the axis. Reverse the order so the largest move
        # sits at the top of the chart.
        labels_rev = list(labels)[::-1]
        values_rev = list(d["Total Capture (%)"])[::-1]
        fig = go.Figure(go.Bar(
            x=values_rev,
            y=labels_rev,
            orientation="h",
            marker={"color": PRJCT9_GREEN},
        ))
        if compact:
            fig.update_layout(
                paper_bgcolor=PRJCT9_BLACK,
                plot_bgcolor=PRJCT9_BLACK,
                font={"color": PRJCT9_TEXT,
                      "family": "Consolas, monospace",
                      "size": 10},
                xaxis={"gridcolor": PRJCT9_BORDER, "title": ""},
                yaxis={"gridcolor": PRJCT9_BORDER, "title": "",
                       "automargin": True},
                margin={"l": 60, "r": 12, "t": 22, "b": 22},
                height=150,
                title={"text": "Best matches",
                       "font": {"color": PRJCT9_GREEN, "size": 11}},
            )
        else:
            fig.update_layout(
                paper_bgcolor=PRJCT9_BLACK,
                plot_bgcolor=PRJCT9_BLACK,
                font={"color": PRJCT9_TEXT, "family": "Consolas, monospace"},
                xaxis={"gridcolor": PRJCT9_BORDER,
                       "title": "Total move (%)"},
                yaxis={"gridcolor": PRJCT9_BORDER, "title": "Signal source",
                       "automargin": True},
                margin={"l": 80, "r": 16, "t": 28, "b": 40},
                height=360,
                title={"text": "Best historical matches",
                       "font": {"color": PRJCT9_GREEN, "size": 13}},
            )
        return dcc.Graph(figure=fig, config={"displayModeBar": False})

    def _render_cumulative_capture_chart(selected_row, meta):
        """Primary chart for the Selected Pattern panel: real
        cumulative capture over time for the (signal source, target)
        pair.

        Source preference:
          1. Saved Phase 6B-1 day-by-day artifact at
             ``output/research_artifacts/impactsearch/<TARGET>/<SRC>.research_day.json``.
             Engine-canonical: same daily path used to compute the
             saved Total Capture (%).
          2. Reconstructed fallback via
             ``_selected_pattern_cumulative_capture`` (separate
             stable signal library + Spymaster cache).

        Honest fallback panel when neither source resolves."""
        signal_source = None
        target = None
        if isinstance(selected_row, dict):
            signal_source = (
                selected_row.get("Primary Ticker")
                or selected_row.get("primary_ticker")
            )
            target = (
                selected_row.get("Secondary Ticker")
                or selected_row.get("secondary_ticker")
            )
        if not target:
            target = (meta or {}).get("target") or DEFAULT_TARGET
        target = str(target).strip().upper() if target else ""
        signal_source = (
            str(signal_source).strip().upper() if signal_source else ""
        )

        empty_msg = html.Div(
            style={
                "border": f"1px solid {PRJCT9_BORDER}",
                "padding": "10px 12px",
                "marginTop": "10px",
            },
            children=[
                html.Div("CUMULATIVE CAPTURE",
                         style={"color": PRJCT9_GREEN,
                                "letterSpacing": "2px",
                                "fontSize": "11px",
                                "marginBottom": "4px"}),
                html.Div(
                    "Cumulative capture chart needs saved daily "
                    "signal history for this signal source.",
                    style={"color": PRJCT9_TEXT,
                           "fontSize": "12px",
                           "lineHeight": "1.5"},
                ),
            ],
        )

        if not signal_source or not target:
            return empty_msg

        dates: list = []
        signals: list = []
        cum: list = []
        daily: list = []
        chart_source = "rebuilt"
        # Phase 6B-1: prefer the saved canonical artifact when present.
        artifact = _read_research_day_artifact_for_pair(
            signal_source, target,
        )
        if artifact is not None and artifact.daily:
            chart_source = "artifact"
            for row in artifact.daily:
                dates.append(row.get("date"))
                signals.append(row.get("signal"))
                cum.append(row.get("cumulative_capture_pct"))
                daily.append(row.get("daily_capture_pct"))
        else:
            df = _selected_pattern_cumulative_capture(
                signal_source, target,
            )
            if df is None or df.empty:
                return empty_msg
            dates = list(df["date"])
            signals = list(df["signal"])
            cum = list(df["cum_capture"])
            daily = list(df["daily_capture"])

        try:
            import plotly.graph_objects as go
        except Exception:
            return empty_msg

        hover_text = []
        for d, s, dc, cc in zip(dates, signals, daily, cum):
            try:
                ds = pd.Timestamp(d).strftime("%Y-%m-%d")
            except Exception:
                ds = str(d)
            try:
                dc_f = float(dc)
            except Exception:
                dc_f = 0.0
            try:
                cc_f = float(cc)
            except Exception:
                cc_f = 0.0
            hover_text.append(
                f"{ds}<br>"
                f"Signal: {s}<br>"
                f"Daily Capture: {dc_f:.4f}%<br>"
                f"Cumulative Capture: {cc_f:.4f}%"
            )
        fig = go.Figure(go.Scatter(
            x=list(dates), y=list(cum),
            mode="lines",
            line={"color": PRJCT9_GREEN, "width": 1.4},
            hovertext=hover_text,
            hoverinfo="text",
        ))
        fig.add_hline(
            y=0.0, line_color=PRJCT9_BORDER, line_width=1,
        )
        fig.update_layout(
            paper_bgcolor=PRJCT9_BLACK,
            plot_bgcolor=PRJCT9_BLACK,
            font={"color": PRJCT9_TEXT, "family": "Consolas, monospace"},
            xaxis={"gridcolor": PRJCT9_BORDER, "title": "Date"},
            yaxis={"gridcolor": PRJCT9_BORDER,
                   "title": "Cumulative Capture (%)"},
            margin={"l": 56, "r": 12, "t": 28, "b": 36},
            height=190,
            title={
                "text": (
                    f"Cumulative Capture - {signal_source} signal on "
                    f"{target}"
                ),
                "font": {"color": PRJCT9_GREEN, "size": 12},
            },
        )
        return html.Div(
            id="cumulative-capture-chart",
            style={"marginTop": "10px",
                   "border": f"1px solid {PRJCT9_BORDER}",
                   "padding": "6px 8px"},
            children=[
                # Plain-language source line: "Chart data: exact saved
                # path" when the saved Phase 6B-1 artifact rendered;
                # "Chart data: rebuilt from local signal files" when
                # the reconstruction fallback rendered. Read by the
                # reconcile line below the chart for parity surfacing.
                html.Div(
                    (
                        "Chart data: exact saved path"
                        if chart_source == "artifact"
                        else "Chart data: rebuilt from local signal files"
                    ),
                    id="cumulative-capture-source",
                    style={"color": (
                            PRJCT9_GREEN if chart_source == "artifact"
                            else PRJCT9_MUTED
                          ),
                          "fontSize": "10px",
                          "letterSpacing": "1px",
                          "textTransform": "uppercase",
                          "marginBottom": "4px"},
                ),
                dcc.Graph(figure=fig, config={"displayModeBar": False}),
            ],
        )

    def _render_local_price_chart(selected_row, meta):
        """Secondary chart for the Selected Pattern panel: studied-
        ticker price history. Sourced from the standalone Spymaster
        cache (cache/results/<TARGET>_precomputed_results.pkl).
        Network is never touched. When no local series is available,
        render one honest line instead of faking a chart."""
        target = None
        if isinstance(selected_row, dict):
            target = (selected_row.get("Secondary Ticker")
                      or selected_row.get("secondary_ticker"))
        if not target:
            target = (meta or {}).get("target") or DEFAULT_TARGET
        target = str(target).strip().upper()

        empty_msg = html.Div(
            style={
                "border": f"1px solid {PRJCT9_BORDER}",
                "padding": "10px 12px",
                "marginTop": "10px",
            },
            children=[
                html.Div("PRICE HISTORY",
                         style={"color": PRJCT9_GREEN,
                                "letterSpacing": "2px",
                                "fontSize": "11px",
                                "marginBottom": "4px"}),
                html.Div(
                    "Price chart not available from saved local data yet.",
                    style={"color": PRJCT9_TEXT,
                           "fontSize": "12px",
                           "lineHeight": "1.5"},
                ),
            ],
        )

        df = _local_price_series_for_target(target)
        if df is None or df.empty:
            return empty_msg
        try:
            import plotly.graph_objects as go
        except Exception:
            return empty_msg

        fig = go.Figure(go.Scatter(
            x=df["date"], y=df["close"],
            mode="lines",
            line={"color": PRJCT9_GREEN, "width": 1.2},
            hovertemplate="%{x|%Y-%m-%d}<br>Close: %{y:.2f}<extra></extra>",
        ))
        fig.update_layout(
            paper_bgcolor=PRJCT9_BLACK,
            plot_bgcolor=PRJCT9_BLACK,
            font={"color": PRJCT9_TEXT, "family": "Consolas, monospace"},
            xaxis={"gridcolor": PRJCT9_BORDER, "title": "Date"},
            yaxis={"gridcolor": PRJCT9_BORDER, "title": "Close"},
            margin={"l": 44, "r": 12, "t": 22, "b": 32},
            height=140,
            title={"text": f"{target} price history (saved local data)",
                   "font": {"color": PRJCT9_GREEN, "size": 12}},
        )
        return html.Div(
            style={"marginTop": "10px",
                   "border": f"1px solid {PRJCT9_BORDER}",
                   "padding": "6px 8px"},
            children=[
                dcc.Graph(figure=fig, config={"displayModeBar": False}),
            ],
        )

    # Plain display labels for the Selected Row card. The XLSX source
    # uses internal column names ("Primary Ticker", "Secondary Ticker",
    # "Trigger Days", "Significant 95%") but the first-impression UI
    # speaks the same plain vocabulary as the rest of the screen.
    _SELECTED_ROW_DISPLAY_MAP: list[tuple[str, str]] = [
        ("Signal source", "Primary Ticker"),
        ("Ticker studied", "Secondary Ticker"),
        ("Total Capture (%)", "Total Capture (%)"),
        ("Sharpe Ratio", "Sharpe"),
        ("Signal days", "Trigger Days"),
        ("95% Confidence", "Significant 95%"),
    ]

    def _render_selected_row_card(selected_row, meta):
        """Prominent card showing the user's selected Results row in
        plain language. When no row is selected, shows a friendly
        'click a row' hint."""
        if not selected_row:
            return html.Div(
                style={
                    "border": f"1px solid {PRJCT9_BORDER}",
                    "padding": "20px",
                    "color": PRJCT9_MUTED,
                    "fontSize": "13px",
                    "marginBottom": "8px",
                },
                children=[
                    html.Div("SIGNAL DETAIL",
                             style={"color": PRJCT9_GREEN,
                                    "letterSpacing": "2px",
                                    "fontSize": "12px",
                                    "marginBottom": "10px"}),
                    "Choose a row from Patterns worth a look below to "
                    "see it explained.",
                ],
            )

        primary_label = str(selected_row.get("Primary Ticker") or "?")
        target_label = str(
            selected_row.get("Secondary Ticker") or meta.get("target") or "?"
        )
        evidence = _evidence_label(selected_row)
        evidence_color = (
            PRJCT9_GREEN if evidence == "Strong historical sample"
            else PRJCT9_RED if evidence == "Too few signal days"
            else PRJCT9_MUTED
        )

        # Plain numeric move for the inline sentence. Falls back to a
        # generic phrasing when the column is missing or non-numeric.
        total_move_v = selected_row.get("Total Capture (%)")
        try:
            total_move_str = (
                f"{float(total_move_v):+.2f}%"
                if total_move_v is not None and np.isfinite(float(total_move_v))
                else None
            )
        except Exception:
            total_move_str = None
        if total_move_str:
            inline_sentence = (
                f"When {primary_label} flashed this signal in the past, "
                f"{target_label} moved {total_move_str} during those "
                "signal days."
            )
        else:
            inline_sentence = (
                f"When {primary_label} flashed this signal in the past, "
                f"here is what happened to {target_label}."
            )

        kv_rows = []
        for plain_label, source_col in _SELECTED_ROW_DISPLAY_MAP:
            if source_col not in selected_row:
                continue
            v = selected_row.get(source_col)
            display_v = "-" if v is None or (
                isinstance(v, float) and not np.isfinite(v)
            ) else v
            if isinstance(display_v, float):
                display_v = f"{display_v:.4f}"
            kv_rows.append(
                html.Div(
                    style={
                        "display": "grid",
                        "gridTemplateColumns": "minmax(120px, 180px) 1fr",
                        "padding": "2px 0",
                        "borderBottom": f"1px solid {PRJCT9_BORDER}",
                        "gap": "8px",
                    },
                    children=[
                        html.Span(plain_label,
                                  style={"color": PRJCT9_MUTED,
                                         "fontSize": "10px",
                                         "letterSpacing": "1px",
                                         "textTransform": "uppercase"}),
                        html.Span(str(display_v),
                                  style={"color": PRJCT9_TEXT,
                                         "fontFamily": "Consolas, monospace",
                                         "fontSize": "11px",
                                         "wordBreak": "break-word"}),
                    ],
                )
            )
        # Append the evidence label as a final row
        kv_rows.append(
            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "minmax(120px, 180px) 1fr",
                    "padding": "2px 0",
                    "gap": "8px",
                },
                children=[
                    html.Span("Evidence",
                              style={"color": PRJCT9_MUTED,
                                     "fontSize": "10px",
                                     "letterSpacing": "1px",
                                     "textTransform": "uppercase"}),
                    html.Span(evidence,
                              style={"color": evidence_color,
                                     "fontSize": "11px",
                                     "fontWeight": "bold"}),
                ],
            )
        )

        return html.Div(
            style={
                "border": f"1px solid {PRJCT9_GREEN}",
                "padding": "8px 10px",
                "marginBottom": "6px",
                "backgroundColor": PRJCT9_DIM,
            },
            children=[
                html.Div(
                    style={"display": "flex",
                           "alignItems": "baseline",
                           "marginBottom": "6px",
                           "gap": "8px",
                           "flexWrap": "wrap"},
                    children=[
                        html.Span("SIGNAL DETAIL",
                                  style={"color": PRJCT9_GREEN,
                                         "letterSpacing": "2px",
                                         "fontSize": "10px"}),
                        html.Span(
                            f"Signal from {primary_label}, tested on {target_label}",
                            style={"color": PRJCT9_TEXT,
                                   "fontWeight": "bold",
                                   "fontSize": "12px"},
                        ),
                    ],
                ),
                html.Div(
                    inline_sentence,
                    style={"color": PRJCT9_TEXT,
                           "fontSize": "11px",
                           "lineHeight": "1.5",
                           "marginBottom": "6px"},
                ),
                html.Div(children=kv_rows),
            ],
        )

    return app


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(port: Optional[int] = None) -> None:
    port = int(port or DEFAULT_PORT)
    # Preload the heavy live engine BEFORE Dash registers callbacks.
    # Importing impactsearch inside a callback raises Dash's
    # ImportedInsideCallbackError, so we cache the module at startup
    # and the callback reads from the cache.
    engine_ready = preload_live_engine()
    engine_status = (
        "ready"
        if engine_ready
        else f"unavailable ({_IMPACTSEARCH_IMPORT_ERROR or 'unknown error'})"
    )
    app = build_app()
    print(
        f"PRJCT9 Research Engine - Local Preview\n"
        f"  output dir:    {_output_dir()}\n"
        f"  live engine:   {engine_status}\n"
        f"  url:           http://127.0.0.1:{port}\n"
        f"  ctrl-c to stop\n"
    )
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
