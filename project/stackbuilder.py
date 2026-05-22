#!/usr/bin/env python3
# PRJCT9 - stackbuilder.py
# Lean fastpath: uses existing Signal Library only; no network; optional minimal Dash UI.
# Phases: 1) Preflight  2) Rank All  3) Stack Builder

import os, re, sys, json, math, glob, argparse, time, shutil, threading
import contextvars
import copy
import logging
import tempfile
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any, Union, Mapping, Sequence, Callable
from itertools import combinations, product
import numpy as np
import pandas as pd
# Opt in to future behaviour to avoid 'replace' downcast warnings
pd.set_option('future.no_silent_downcasting', True)
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from scipy import stats
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from canonical_scoring import (
    combine_consensus_signals as _canonical_consensus,
    score_captures as _canonical_score_captures,
    metrics_to_legacy_dict as _canonical_metrics_to_legacy_dict,
)
from provenance_manifest import (
    verify_manifest as _verify_manifest,
    load_verified_signal_library as _load_verified_signal_library,
    build_output_manifest as _build_output_manifest,
    file_sha256 as _file_sha256,
    load_verified_xlsx_artifact as _load_verified_xlsx_artifact,
    MANIFEST_SCHEMA_VERSION as _MANIFEST_SCHEMA_VERSION,
    ARTIFACT_KIND_OUTPUT as _ARTIFACT_KIND_OUTPUT,
)
from validation_engine import (
    FoldContext,
    StrategyCandidate,
    StrategyFoldResult,
    BaselineFoldMetrics,
    validate_strategy_set,
    write_validation_sidecar,
    compute_validation_artifact_hash,
    extract_manifest_summary,
    generate_run_id,
    slice_to_cutoff,
    slice_between,
    DEFAULT_INITIAL_TRAIN_DAYS,
    DEFAULT_TEST_WINDOW_DAYS,
    DEFAULT_STEP_DAYS,
    DEFAULT_ALPHA,
    DEFAULT_BORDERLINE_TOLERANCE_MULTIPLIER,
    DEFAULT_OUTCOME_WINDOWS,
    VALIDATION_CONTRACT_VERSION,
    VALIDATION_METHODOLOGY_VERSION,
    VALIDATION_OUTPUT_BASE_DIR,
)


_validation_logger = logging.getLogger("stackbuilder.validation")


# Phase 3B-2A: per-run collection of input signal-library manifest content
# hashes so the run_manifest.json can pin which libraries this run consumed.
#
# Phase 3B-2A amendment (Codex audit, PR #143): the collector lives in a
# ContextVar so each ``run_for_secondary`` invocation gets its own
# isolated collector. Two consecutive Dash-launched runs (each on its own
# threading.Thread) correctly receive disjoint snapshots.
#
# StackBuilder also loads libraries inside ThreadPoolExecutor pools (see
# phase2_rank_all). ContextVars do NOT automatically propagate from the
# submitter into long-lived executor worker threads, so executor
# submissions that may transitively call ``_record_input_lib`` MUST run
# under ``contextvars.copy_context().run`` so the worker observes the
# submitter's collector. ``_submit_with_context`` below is the wrapper.
_INPUT_COLLECTOR_VAR: "contextvars.ContextVar[Optional[dict]]" = (
    contextvars.ContextVar("stackbuilder_input_collector", default=None)
)


def _start_input_manifest_collection() -> "contextvars.Token":
    """Start a fresh per-run collector and return the ContextVar token.

    Callers should pass the returned token to
    ``_finalize_input_manifest_collection(token)`` so the ContextVar is
    properly reset to its prior value after the run, including in
    finally/except paths where the collection might not have completed.
    """
    collector = {
        "hashes": set(),
        "legacy": 0,
        "missing": 0,
        "lock": threading.RLock(),
    }
    return _INPUT_COLLECTOR_VAR.set(collector)


def _record_input_lib(lib: Optional[dict]) -> None:
    """Record one signal-library load against the current run's collector.

    Reads the ContextVar; no-ops when no run is active. Mutations are
    serialized through the per-collector RLock so concurrent
    ThreadPoolExecutor workers (when wrapped with
    ``contextvars.copy_context().run``) see consistent state.
    """
    coll = _INPUT_COLLECTOR_VAR.get()
    if coll is None:
        return
    with coll["lock"]:
        if lib is None or not isinstance(lib, dict):
            coll["missing"] += 1
            return
        manifest = lib.get("_manifest")
        if not isinstance(manifest, dict):
            coll["legacy"] += 1
            return
        ch = manifest.get("content_hash")
        if isinstance(ch, str) and ch:
            coll["hashes"].add(ch)
        else:
            coll["legacy"] += 1


def _finalize_input_manifest_collection(
    token: "Optional[contextvars.Token]" = None,
) -> dict:
    """Return a snapshot of the current run's collector and reset it.

    When ``token`` is provided, ``_INPUT_COLLECTOR_VAR.reset(token)``
    restores the prior ContextVar value. When no token is supplied, the
    collector is cleared via ``set(None)`` (legacy callers / defensive
    cleanup paths). Either way the snapshot reflects only the current
    run's contributions.
    """
    coll = _INPUT_COLLECTOR_VAR.get() or {
        "hashes": set(), "legacy": 0, "missing": 0,
    }
    if "lock" in coll:
        with coll["lock"]:
            snapshot = {
                "input_manifest_hashes": sorted(coll["hashes"]),
                "input_legacy_count": int(coll["legacy"]),
                "input_missing_manifest_count": int(coll["missing"]),
            }
    else:
        snapshot = {
            "input_manifest_hashes": sorted(coll["hashes"]),
            "input_legacy_count": int(coll["legacy"]),
            "input_missing_manifest_count": int(coll["missing"]),
        }
    if token is not None:
        try:
            _INPUT_COLLECTOR_VAR.reset(token)
        except (ValueError, LookupError):
            # Token came from a different Context (e.g. captured by
            # copy_context().run on a worker thread). Phase 3B-2B:
            # surface this with a logged warning instead of silently
            # falling back, so the audit trail records a possible
            # ContextVar mismanagement signal. Fall back to a plain
            # clear afterwards; the worker's local copy will be
            # discarded when the worker context is released.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Cross-context input-manifest collector token reset "
                "detected; clearing current collector. This may "
                "indicate ContextVar mismanagement."
            )
            _INPUT_COLLECTOR_VAR.set(None)
    else:
        _INPUT_COLLECTOR_VAR.set(None)
    return snapshot


def _submit_with_context(executor, fn, *args, **kwargs):
    """Submit ``fn`` to ``executor`` under the caller's current Context.

    ContextVars do not propagate into ThreadPoolExecutor worker threads
    on their own. This wrapper captures the caller's Context via
    ``contextvars.copy_context()`` and runs the worker callable inside
    it, so the per-run collector ContextVar set by ``run_for_secondary``
    is observable in the worker.
    """
    ctx = contextvars.copy_context()
    return executor.submit(ctx.run, fn, *args, **kwargs)

try:
    import yfinance as yf
except ImportError:
    yf = None

# ---------- Optional project imports (fallbacks if absent) ----------
def _try_import():
    try:
        from signal_library.shared_symbols import resolve_symbol, detect_ticker_type
    except Exception:
        try:
            from shared_symbols import resolve_symbol, detect_ticker_type  # type: ignore
        except Exception:
            def resolve_symbol(t: str) -> Tuple[str, str]:
                t2 = (t or "").strip().upper()
                return t2, t2
            def detect_ticker_type(t: str) -> str:
                return 'crypto' if (t or '').upper().endswith('-USD') else 'equity'
    try:
        from onepass import load_signal_library
    except Exception:
        load_signal_library = None
    return resolve_symbol, detect_ticker_type, load_signal_library
resolve_symbol, detect_ticker_type, load_signal_library = _try_import()

# ---------- Config ----------
# Project-relative anchor: this file lives at project/stackbuilder.py, so
# Path(__file__).resolve().parent IS the project directory.
_PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SIGNAL_LIB_DIR = os.environ.get(
    'SIGNAL_LIBRARY_DIR',
    str(_PROJECT_DIR / 'signal_library' / 'data' / 'stable')
)
DEFAULT_PRICE_CACHE_DIR = os.environ.get('PRICE_CACHE_DIR', 'price_cache/daily')  # e.g., {TICKER}.parquet
MASTER_TICKERS_PATH = os.environ.get('YF_MASTER_TICKERS_PATH', os.environ.get('MASTER_TICKERS_PATH', 'global_ticker_library/data/master_tickers.txt'))

RUNS_ROOT = 'output/stackbuilder'
RISK_FREE_ANNUAL = 5.0  # percent
FLOAT_DTYPE = np.float64
# ImpactSearch Excel defaults (override via CLI or env var)
DEFAULT_IMPACT_XLSX_DIR = os.environ.get(
    'PRJCT9_IMPACT_XLSX_DIR',
    str(_PROJECT_DIR / 'output' / 'impactsearch')
)
# output format default; can be overridden by --output-format
OUTPUT_FORMAT = os.environ.get("STACKBUILDER_OUTPUT_FORMAT", "xlsx").lower()
DEFAULT_GRACE_DAYS = int(os.environ.get('IMPACT_CALENDAR_GRACE_DAYS', '10') or 10)


def _effective_grace_days(grace_days):
    """Resolve a per-call grace override against DEFAULT_GRACE_DAYS.

    Phase 2B-2B: explicit grace plumbing (Entry 7 amendment). Callers
    pass ``grace_days=None`` to mean "use the spec-default 10 days";
    any concrete int (including 0) is honored as-is. ``run_for_secondary``
    resolves the value once from ``args.grace_days`` and threads the
    concrete int through ``phase2_rank_all`` / ``phase3_build_stacks``
    instead of mutating ``os.environ['IMPACT_CALENDAR_GRACE_DAYS']``.
    """
    return DEFAULT_GRACE_DAYS if grace_days is None else int(grace_days)


# runtime-mutable signal dir (set in main from CLI)
SIGNAL_LIB_DIR_RUNTIME = DEFAULT_SIGNAL_LIB_DIR

# --- Progress + trigger-scope knobs (lightweight, no extra deps) ---
PROGRESS_ROOT = os.path.join(RUNS_ROOT, "_progress")
# Count trigger days from the combined signal itself, not intersection of members.
# "BUY+NONE" or "SHORT+NONE" are allowed; any BUY+SHORT mix => NONE.
COMBINE_INTERSECTION = False
VERBOSE = False  # Global verbose flag, set from CLI

def _write_progress(progress_path: str, **payload):
    """
    Atomic file-backed progress with field preservation.
    Keeps 'started_ts' and prior keys unless explicitly overridden.
    Uses temp file + os.replace for safe concurrent writes.
    """
    try:
        ensure_dir(os.path.dirname(progress_path))
        prior = {}
        try:
            if os.path.exists(progress_path):
                with open(progress_path, "r", encoding="utf-8") as _f:
                    prior = json.load(_f) or {}
        except Exception:
            prior = {}
        prior.update(payload)
        if 'started_ts' not in prior and str(prior.get('status','')) == 'running':
            prior['started_ts'] = time.time()
        prior['ts'] = time.time()
        # Atomic write: temp file + replace
        tmp_path = progress_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(prior, f, indent=2)
        os.replace(tmp_path, progress_path)
    except Exception:
        pass

# ---------- IO helpers ----------
def ensure_dir(p: str) -> None:
    Path(p).mkdir(parents=True, exist_ok=True)

def write_json(path: str, obj) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2)


# Phase 3B-2A: helpers for run_manifest output_artifact entries.
def _stable_cli_args_subset(args) -> dict:
    """Subset of args that materially affects output content.

    Excludes ephemeral / display-only fields (progress paths, --outdir,
    process IDs) so the same logical run produces the same fingerprint
    across re-runs from different working directories.
    """
    fields = (
        "alpha", "max_k", "top_n", "bottom_n", "min_trigger_days",
        "sharpe_eps", "seed_by", "search", "combine_mode",
        "grace_days",
    )
    out: dict = {}
    for fname in fields:
        if hasattr(args, fname):
            v = getattr(args, fname)
            if v is None:
                out[fname] = None
            elif isinstance(v, (int, float, str, bool)):
                out[fname] = v
            else:
                out[fname] = str(v)
    return out


def _output_artifact_entry(
    outdir: str,
    name: str,
    *,
    candidate_extensions=("xlsx", "csv", "parquet", "json"),
    content_hasher=None,
) -> Optional[dict]:
    """Build one entry for run_manifest.output_artifacts.

    Tries each candidate extension; the first match wins. Returns None
    when the artifact is absent. ``content_hasher`` is an optional
    callable that returns a logical content_hash string given the path
    (e.g. for tabular files we may skip a logical hash and rely on the
    file_sha256 byte-level integrity).
    """
    for ext in candidate_extensions:
        candidate = os.path.join(outdir, f"{name}.{ext}")
        if not os.path.exists(candidate):
            continue
        entry = {
            "name": name,
            "filename": os.path.basename(candidate),
            "format": ext,
            "file_sha256": _file_sha256(candidate),
            "produced_at": datetime.now().isoformat(),
        }
        try:
            entry["size_bytes"] = int(os.path.getsize(candidate))
        except OSError:
            entry["size_bytes"] = None
        # Quick row/column shape probe for tabular outputs without re-
        # parsing the full file. xlsx/parquet schema probing is left
        # for the future (Phase 3B-2B); CSV is cheap to count.
        if ext == "csv":
            try:
                with open(candidate, "r", encoding="utf-8") as fh:
                    header = fh.readline().rstrip("\r\n")
                    line_count = sum(1 for _ in fh)
                entry["row_count"] = int(line_count)
                entry["column_schema"] = [
                    {"name": col} for col in header.split(",")
                ]
            except OSError:
                pass
        if content_hasher is not None:
            try:
                entry["content_hash"] = content_hasher(candidate)
            except Exception:
                pass
        return entry
    return None


def _build_output_artifacts(outdir: str) -> list:
    """Scan a finalized StackBuilder run directory for output artifacts."""
    artifacts: list = []
    # Phase 6I-73: rank_inverse is no longer a persisted output artifact.
    # Bottom_n inverse candidates are derived internally from the
    # most-negative Total Capture rows of rank_direct and live only as
    # an in-memory bounded cohort frame.
    table_names = (
        "rank_all", "rank_direct", "cohort", "combo_leaderboard",
    )
    for name in table_names:
        entry = _output_artifact_entry(outdir, name)
        if entry is not None:
            artifacts.append(entry)
    for name in ("summary", "search_stats"):
        entry = _output_artifact_entry(
            outdir, name, candidate_extensions=("json",),
        )
        if entry is not None:
            artifacts.append(entry)
    return artifacts

def write_table(df: pd.DataFrame, basepath: str) -> None:
    ensure_dir(os.path.dirname(basepath))
    fmt = (OUTPUT_FORMAT or "xlsx").lower()
    if fmt == "xlsx":
        try:
            df.to_excel(basepath + ".xlsx", index=False)
            return
        except Exception as e:
            print(f"[WARN] Excel write failed ({e}); falling back to CSV.")
            df.to_csv(basepath + ".csv", index=False); return
    if fmt == "parquet":
        try:
            df.to_parquet(basepath + ".parquet", index=False); return
        except Exception as e:
            print(f"[WARN] Parquet write failed ({e}); falling back to CSV.")
    df.to_csv(basepath + ".csv", index=False)

def now_ts() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M%S')

# ---------- JSON helpers ----------
def _json_safe_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Convert a DataFrame to Dash/JSON friendly records.
    Ensures all values are JSON-serializable Python native types.
    """
    import numpy as _np
    import math as _math
    out = []
    for _, row in df.iterrows():
        rec = {}
        for c, v in row.items():
            # Handle lists/arrays first (Members field)
            if isinstance(v, (list, tuple, _np.ndarray)):
                if c == 'Members':
                    v = ", ".join(str(x) for x in v)
                else:
                    v = list(v)  # Convert to Python list
            # Check for NaN only on scalar values
            elif pd.isna(v):
                v = None
            elif isinstance(v, (_np.integer,)):
                v = int(v)
            elif isinstance(v, (_np.floating, _np.float32, _np.float64)):
                v = float(v)
                if _math.isnan(v) or _math.isinf(v):
                    v = None
            elif isinstance(v, (_np.bool_,)):
                v = bool(v)
            elif isinstance(v, float):
                # Handle Python native floats
                if _math.isnan(v) or _math.isinf(v):
                    v = None
            rec[str(c)] = v
        out.append(rec)
    return out

# ---------- Universe discovery ----------
def load_master_universe() -> List[str]:
    if os.path.exists(MASTER_TICKERS_PATH):
        raw = Path(MASTER_TICKERS_PATH).read_text(encoding='utf-8').upper()
        toks = re.split(r'[\s,]+', raw)
        return [t for t in toks if t]
    return []

def discover_from_signal_library() -> List[str]:
    tickers = set()
    # match both flat and sharded layouts
    pats = [
        os.path.join(DEFAULT_SIGNAL_LIB_DIR, "*_stable_v*.pkl"),
        os.path.join(DEFAULT_SIGNAL_LIB_DIR, "*", "*_stable_v*.pkl"),
        os.path.join(DEFAULT_SIGNAL_LIB_DIR, "*", "*_signal_library.pkl"),
    ]
    for pat in pats:
        for p in glob.glob(pat):
            name = os.path.basename(p)
            t = name.split("_stable_")[0].split("_signal_library")[0]
            if t:
                tickers.add(t.upper())
    return sorted(tickers)

def primary_universe(specified_tickers: Optional[List[str]] = None) -> List[str]:
    """
    Return ONLY the explicitly provided tickers when the UI supplies any list
    (including an empty list). Fall back to discovery only when None.
    """
    # Respect explicit UI intent: None => allow fallback; [] or list => use as-is
    if specified_tickers is not None:
        # De-dup while preserving case like '^' symbols already uppercased by UI
        seen, out = set(), []
        for t in specified_tickers:
            u = str(t).upper()
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out
    # Otherwise fall back to master list or discovery
    universe = load_master_universe()
    if universe:
        return universe
    fallback = discover_from_signal_library()
    if fallback:
        return fallback
    print("[WARN] No master list or libraries discovered; primary universe is empty.")
    return []

# ---------- Data loading ----------
# Raw Close only (spec v0.5 §3, ledger Entry 1).
def _fetch_secondary_from_yf(secondary: str) -> pd.DataFrame:
    if yf is None:
        raise RuntimeError("yfinance not installed. Install with: pip install yfinance")
    sym = (secondary or "").upper()  # keep caret for indices like ^VIX
    df = yf.download(sym, period="max", interval="1d", auto_adjust=False, progress=False, threads=True)
    if df is None or len(df) == 0:
        raise RuntimeError(f"yfinance returned no data for {sym}")
    df = df.rename_axis('Date').reset_index().set_index('Date')
    df.index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(None) if getattr(pd.Timestamp(d), "tz", None) else pd.Timestamp(d) for d in df.index])

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close_col = None
    for col in df.columns:
        if str(col).lower() == 'close':
            close_col = col
            break

    if close_col is None:
        raise RuntimeError(f"yfinance returned no Close column for {sym}. Columns: {list(df.columns)}")
    out = pd.DataFrame(df[close_col]).rename(columns={close_col: 'Close'})
    return out.astype(FLOAT_DTYPE)

def load_secondary_prices(secondary: str) -> pd.DataFrame:
    sec = (secondary or "").upper()
    sec_clean = sec.replace("^", "")
    cands = [
        os.path.join(DEFAULT_PRICE_CACHE_DIR, f"{sec}.parquet"),
        os.path.join(DEFAULT_PRICE_CACHE_DIR, f"{sec}.csv"),
        os.path.join(DEFAULT_PRICE_CACHE_DIR, f"{sec_clean}.parquet"),
        os.path.join(DEFAULT_PRICE_CACHE_DIR, f"{sec_clean}.csv"),
        os.path.join(DEFAULT_PRICE_CACHE_DIR, sec, "daily.parquet"),
    ]
    for p in cands:
        if os.path.exists(p):
            df = pd.read_parquet(p) if p.endswith('.parquet') else pd.read_csv(p)
            if 'Date' in df.columns:
                df['Date'] = pd.to_datetime(df['Date'])
                df = df.set_index('Date')
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            else:
                df.index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(None) if getattr(pd.Timestamp(d), "tz", None) else pd.Timestamp(d) for d in df.index])
            df = df.sort_index()
            if 'Close' not in df.columns:
                raise ValueError(f"{p}: missing Close")
            out = pd.DataFrame(df['Close'])
            out = out[~out.index.duplicated(keep='last')].astype(FLOAT_DTYPE)
            return out
    return _fetch_secondary_from_yf(sec)

def pct_returns(close: pd.Series) -> pd.Series:
    return close.pct_change().fillna(0.0).astype(FLOAT_DTYPE) * 100.0  # percent

# ---------- ImpactSearch XLSX fast-path ----------
_RANK_COLMAP = {
    'primary':'Primary Ticker','primaryticker':'Primary Ticker','ticker':'Primary Ticker',
    'total capture':'Total Capture (%)','total capture (%)':'Total Capture (%)',
    'avg daily capture':'Avg Daily Capture (%)','avg daily capture (%)':'Avg Daily Capture (%)',
    'win ratio':'Win Ratio (%)','win ratio (%)':'Win Ratio (%)',
    'std dev (%)':'Std Dev (%)','sharpe':'Sharpe Ratio','sharpe ratio':'Sharpe Ratio',
    'p':'p-Value','p-value':'p-Value','p value':'p-Value','trigger days':'Trigger Days','triggers':'Trigger Days'
}
def _standardize_rank_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols = {}
    for c in df.columns:
        k = str(c).strip().lower()
        cols[c] = _RANK_COLMAP.get(k, c)
    out = df.rename(columns=cols)
    need = ['Primary Ticker','Trigger Days','Win Ratio (%)','Std Dev (%)',
            'Sharpe Ratio','Avg Daily Capture (%)','Total Capture (%)','p-Value']
    have = [c for c in need if c in out.columns]
    if 'Primary Ticker' not in have or 'Total Capture (%)' not in have:
        raise ValueError("ImpactSearch XLSX missing required columns")
    return out[have].copy()

def try_load_rank_from_impact_xlsx(
    sec: str,
    dirpath: str,
    max_age_days: int,
    *,
    strict_manifests: bool = False,
    rejection_out: Optional[Dict[str, Any]] = None,
) -> Optional[pd.DataFrame]:
    """Load ImpactSearch Excel ONLY if it matches the selected secondary ticker.

    Phase 3B-2B: optional manifest verification via
    ``load_verified_xlsx_artifact``. Behavior:

      - non-strict + missing/legacy manifest -> warn, use fast-path
      - non-strict + present-but-mismatched manifest -> warn, reject
        fast-path (return None) so the caller falls back to slow path
      - non-strict + legacy_row_count > 0 -> warn, use fast-path
      - strict + missing/legacy/mismatched -> reject fast-path (None)
      - strict + legacy_row_count > 0 -> reject fast-path (None)

    Post Phase 3 cleanup: if ``rejection_out`` is supplied, the caller
    receives structured rejection info on a None return so it can
    distinguish "not found" from "found but rejected as stale" (and emit
    an actionable error message). Keys populated: ``reason`` (str),
    plus reason-specific fields (e.g. ``path``, ``age_days``,
    ``max_age_days`` for ``"stale"``). Absent / empty dict means "no
    rejection recorded by this call".
    """
    try:
        if not dirpath or not os.path.isdir(dirpath):
            return None
        sec_up = (sec or '').upper()
        sec_clean = sec_up.replace('^','')

        # Only consider files whose name references the selected secondary
        cands = []
        for fn in os.listdir(dirpath):
            if fn.lower().endswith('.xlsx'):
                p = os.path.join(dirpath, fn)
                try:
                    mtime = os.path.getmtime(p)
                except Exception:
                    continue
                base = fn.upper()
                # Must match the secondary ticker name exactly at start of filename
                if base.startswith(sec_up + '_') or base.startswith(sec_clean + '_'):
                    cands.append((mtime, p, base))

        if not cands:
            # No matching workbook for this secondary
            return None

        # Choose the freshest matching workbook
        mtime, best, base = max(cands, key=lambda x: x[0])

        # Staleness gate
        age_days = (time.time() - mtime) / 86400.0
        if max_age_days and age_days > max_age_days:
            print(f"[INFO] ImpactSearch XLSX too old (> {max_age_days}d): {best}")
            if rejection_out is not None:
                rejection_out['reason'] = 'stale'
                rejection_out['path'] = best
                rejection_out['age_days'] = age_days
                rejection_out['max_age_days'] = max_age_days
            return None

        # Phase 3B-2B: manifest verification before fast-path use.
        verified_df, vresult = _load_verified_xlsx_artifact(
            best, strict=strict_manifests,
        )
        if verified_df is None:
            # Hard load error (corrupt workbook, missing file). Reject.
            print(
                f"[WARN] ImpactSearch XLSX load error at {best}: "
                f"{vresult.mismatches}"
            )
            return None
        if vresult.legacy:
            if strict_manifests:
                print(
                    f"[STRICT] ImpactSearch XLSX has no provenance manifest "
                    f"at {best}; rejecting fast-path under "
                    f"--strict-manifests."
                )
                return None
            print(
                f"[WARN] ImpactSearch XLSX has no provenance manifest at "
                f"{best} (legacy); proceeding with fast-path."
            )
        elif not vresult.ok:
            # Manifest exists but does not verify -- always reject the
            # fast-path (mismatches indicate the workbook bytes or
            # logical content disagree with the recorded manifest).
            print(
                f"[WARN] ImpactSearch XLSX provenance manifest mismatch at "
                f"{best}: {vresult.mismatches}. Rejecting fast-path."
            )
            return None
        # legacy_row_count warnings under non-strict still allow fast-path;
        # strict mode treats them as mismatches above.
        for w in vresult.warnings:
            if isinstance(w, tuple) and w and "legacy_row_count" in str(w[0]):
                print(
                    f"[WARN] ImpactSearch XLSX has {w[2]} retained "
                    f"legacy row(s) at {best}; partial provenance "
                    f"coverage. Rerun ImpactSearch to refresh."
                )

        df = _standardize_rank_columns(verified_df)
        for c in ['Avg Daily Capture (%)','Total Capture (%)','Sharpe Ratio','Win Ratio (%)','Std Dev (%)','Trigger Days']:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['Primary Ticker', 'Total Capture (%)']).reset_index(drop=True)
        print(f"[FASTPATH] Using ImpactSearch ranking: {best}  rows={len(df)}")
        return df
    except Exception as e:
        print(f"[WARN] Failed to load ImpactSearch XLSX: {e}")
        return None

def list_signal_library_candidates(ticker: str) -> List[str]:
    root = SIGNAL_LIB_DIR_RUNTIME or DEFAULT_SIGNAL_LIB_DIR
    pat1 = os.path.join(root, f"{ticker}_stable_v*.pkl")
    pat2 = os.path.join(root, ticker[:2].upper() if len(ticker) >= 2 else ticker.upper(), f"{ticker}_signal_library.pkl")
    return glob.glob(pat1) + glob.glob(pat2)

def fallback_load_signal_library(ticker: str) -> Optional[dict]:
    """
    Phase 3B-1: glob-based fallback loader for signal libraries when
    onepass.load_signal_library is unavailable. Routes through the
    central verified loader so the raw pickle.load, type check, and
    manifest verification all live in provenance_manifest. Manifest
    mismatches skip the candidate; legacy libraries warn and load.
    """
    for p in list_signal_library_candidates(ticker):
        try:
            lib, _vresult = _load_verified_signal_library(
                p,
                requested_params={
                    'price_source': 'Close',
                },
            )
        except Exception:
            continue
        if lib is None:
            continue
        if _vresult.legacy:
            # Legacy libraries (pre-Phase-3A) load with a warning.
            # Print to stderr-equivalent via the existing print
            # infrastructure rather than logger to match the rest
            # of stackbuilder's IO pattern.
            print(f"[WARN] {ticker}: legacy signal library at {p} "
                  f"(no provenance manifest)")
            return lib
        if not _vresult.ok:
            print(f"[WARN] {ticker}: provenance manifest mismatch at "
                  f"{p}: {_vresult.mismatches}. Skipping.")
            continue
        return lib
    return None

def load_lib_or_none(t: str) -> Optional[dict]:
    if load_signal_library:
        try:
            lib = load_signal_library(t)
            if lib:
                _record_input_lib(lib)
                return lib
        except Exception:
            pass
    lib = fallback_load_signal_library(t)
    _record_input_lib(lib)
    return lib

# ---------- Signal application and metrics ----------
def apply_signals_to_secondary(primary_signals: List[str], primary_dates: List, sec_returns: pd.Series, *, return_mask: bool = False, grace_days: Optional[int] = None):
    """
    ImpactSearch-parity alignment:
      - align to secondary index
      - carry forward signals within a grace window (default 10 calendar days)
      - fill missing with 'None'

    Phase 2B-2A: when ``return_mask=True`` the function returns
    ``(captures, trigger_mask)`` where trigger_mask is a boolean
    Series on ``sec_returns.index`` marking each Buy/Short signal day.
    Default ``return_mask=False`` returns only ``captures`` for
    backward compatibility with callers that compute their own mask
    or use the legacy single-arg metrics_from_captures fallback.

    Phase 2B-2B: ``grace_days`` is now an explicit kwarg. ``None``
    falls back to ``DEFAULT_GRACE_DAYS`` via ``_effective_grace_days``;
    any int (including 0) is honored as-is. Run-orchestration
    resolves the value once and threads it through.
    """
    if not primary_signals or not primary_dates or sec_returns.empty:
        empty = pd.Series(dtype=FLOAT_DTYPE)
        if return_mask:
            return empty, pd.Series(dtype=bool)
        return empty

    # Normalize indices (drop tz if present)
    idx = pd.DatetimeIndex(pd.to_datetime(primary_dates))
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    sidx = sec_returns.index
    if getattr(sidx, "tz", None) is not None:
        sidx = sidx.tz_localize(None)

    sigs = pd.Series(list(primary_signals), index=idx)

    # Resolve grace window: explicit kwarg wins; None -> DEFAULT_GRACE_DAYS.
    grace_days = _effective_grace_days(grace_days)
    if grace_days > 0:
        # Reindex with forward fill within tolerance window
        aligned = sigs.reindex(sidx, method='pad', tolerance=pd.Timedelta(days=grace_days))
    else:
        aligned = sigs.reindex(sidx, method=None)

    # Fill missing with 'None'
    aligned = aligned.fillna('None').astype(str).str.strip()
    # Diagnostics: how many trigger days are from exact dates vs grace padding
    try:
        exact_mask = aligned.index.isin(idx)
        trig = aligned.isin(['Buy','Short'])
        exact_trig = int((trig & exact_mask).sum())
        padded_trig = int((trig & ~exact_mask).sum())
        if os.environ.get('IMPACT_DEBUG_ALIGN', '0') == '1':
            print(f"[ALIGN] TriggerDays exact={exact_trig} padded={padded_trig} grace_days={grace_days}")
    except Exception:
        pass

    buy = aligned.eq('Buy')
    short = aligned.eq('Short')
    captures = pd.Series(0.0, index=sec_returns.index, dtype=FLOAT_DTYPE)
    captures.loc[buy] = sec_returns.loc[buy]
    captures.loc[short] = -sec_returns.loc[short]
    captures = captures.fillna(0.0)
    if return_mask:
        trigger_mask = (buy | short).astype(bool)
        # Reindex the boolean mask onto the captures index defensively
        # (pandas may have a different index type after the operation).
        trigger_mask = trigger_mask.reindex(captures.index, fill_value=False)
        return captures, trigger_mask
    return captures

def metrics_from_captures(captures: pd.Series, trigger_mask: Optional[pd.Series] = None) -> Optional[Dict[str, float]]:
    """Compute canonical metrics from a daily-capture series.

    Delegates to canonical_scoring.score_captures (spec §13–§17).

    DEPRECATION NOTE (single-arg fallback):
      Canonical callers MUST pass an explicit `trigger_mask` constructed
      from the signal-state series (Buy or Short). The single-arg form
      `metrics_from_captures(captures)` is a legacy compatibility path
      that uses `captures.ne(0.0)` as a stand-in mask, which incorrectly
      drops zero-capture trigger days (spec §15: zero-capture days under
      an active position are still trigger days, counted as losses).
      The fallback is retained only to keep external callers compiling
      until they have been plumbed with signal info; it will be removed
      in a follow-up PR once every external caller passes a real
      trigger mask.
    """
    if captures.empty:
        return None
    if trigger_mask is None:
        mask = captures.ne(0.0)
    else:
        mask = trigger_mask.reindex(captures.index).fillna(False).astype(bool)
    if int(mask.sum()) == 0:
        return None
    score = _canonical_score_captures(
        captures.astype(FLOAT_DTYPE),
        mask,
        risk_free_rate=RISK_FREE_ANNUAL,
        periods_per_year=252,
        ddof=1,
    )
    return _canonical_metrics_to_legacy_dict(score)

# ---------- Phase 1: Preflight ----------
def phase1_preflight(args, secondary: str, specified_primaries: Optional[List[str]] = None):
    vendor_secondary, _ = resolve_symbol(secondary)
    sec_df = load_secondary_prices(vendor_secondary)
    if sec_df.empty:
        raise RuntimeError(f"Unable to load prices for {vendor_secondary}.")
    sec_rets = pct_returns(sec_df['Close'])

    # CRITICAL: Strictly require user primaries; no 72k fallback
    if specified_primaries is not None:
        primaries = primary_universe(specified_primaries)
        if not primaries:
            # If fast-path Excel will be used, allow empty primaries (will filter Excel)
            if getattr(args, "prefer_impact_xlsx", False):
                print("[INFO] Will attempt to use ImpactSearch Excel for primaries.")
                primaries = []
            else:
                raise SystemExit("[FATAL] No primary tickers provided. "
                                 "Enter tickers in the Primary field or enable 'Use ImpactSearch .xlsx'.")
        else:
            print(f"[PHASE1] Using {len(primaries)} user-specified primaries: {', '.join(primaries[:10])}"
                  f"{' ...' if len(primaries) > 10 else ''}")
    else:
        # No primaries specified at all - allow if using ImpactSearch xlsx
        if getattr(args, "prefer_impact_xlsx", False):
            print("[INFO] No primaries specified, will use all from ImpactSearch Excel.")
            primaries = []
        else:
            raise SystemExit("[FATAL] Primary tickers field is empty. Please supply one or more primaries.")

    primaries_df = pd.DataFrame({'Primary Ticker': primaries})
    return primaries_df, sec_rets, vendor_secondary

# ---------- Phase 2: Rank All ----------
def _flip_signals(signals):
    """Phase 2B-2B: relabel Buy<->Short, leave None untouched.

    Used to convert a primary's direct signals into the equivalent
    inverse-mode signals so that downstream scoring produces a real
    inverse-mode metric (Sharpe with the correct risk-free-rate sign,
    proper trigger-mask, etc.) rather than the legacy negate-and-view
    of direct metrics.

    Accepts a list/iterable of string labels or int8-encoded values.
    Returns the same shape: list of strings ('Buy', 'Short', 'None')
    when input is strings; list of ints (1, -1, 0) when input is ints.
    Empty / None input passes through unchanged.
    """
    if signals is None:
        return signals
    seq = list(signals)
    if not seq:
        return seq
    if isinstance(seq[0], (int, np.integer)):
        # Int form: 1=Buy, -1=Short, 0=None. Negate 1<->-1; preserve 0.
        return [(-int(s) if int(s) in (-1, 1) else 0) for s in seq]
    # String form.
    out = []
    for s in seq:
        if s == 'Buy':
            out.append('Short')
        elif s == 'Short':
            out.append('Buy')
        else:
            out.append('None')
    return out


def _load_primary_signals(
    primary: str,
    *,
    data_available_through: Optional[pd.Timestamp] = None,
) -> Tuple[str, Optional[List[str]], Optional[List]]:
    """Phase 2B-2B: load + decode a primary's signal library once.

    Returns ``(vendor, sigs, dates)`` where ``sigs`` is a list of
    string labels ('Buy' / 'Short' / 'None') and ``dates`` is the
    library's original date list. ``sigs`` and ``dates`` are ``None``
    if no library is available or the library is missing required
    fields. Centralizing this load lets ``phase2_rank_all`` score
    both modes from the same payload without duplicating IO.

    Phase 5C-2c: ``data_available_through`` (optional) restricts the
    returned ``(sigs, dates)`` to ``date <= data_available_through``.
    Default ``None`` preserves byte-identical behavior so production
    runs are unaffected; validation folds pass an explicit cutoff.
    """
    vendor, _ = resolve_symbol(primary)
    lib = load_lib_or_none(vendor)
    if not lib:
        return vendor, None, None
    sigs = lib.get('primary_signals') or lib.get('primary_signals_int8')
    dates = lib.get('dates') or lib.get('date_index')
    if not sigs or not dates:
        return vendor, None, None
    if isinstance(sigs[0], (int, np.integer)):
        dec = {1: 'Buy', -1: 'Short', 0: 'None'}
        sigs = [dec.get(int(x), 'None') for x in sigs]
    sigs_list = list(sigs)
    dates_list = list(dates)
    if data_available_through is not None:
        cutoff_ts = pd.Timestamp(data_available_through)
        try:
            date_idx = pd.to_datetime(dates_list)
        except Exception:
            return vendor, sigs_list, dates_list
        if getattr(date_idx, "tz", None) is not None:
            date_idx = date_idx.tz_localize(None)
        keep = (date_idx <= cutoff_ts)
        if not bool(np.any(keep)):
            return vendor, [], []
        sigs_list = [s for s, k in zip(sigs_list, keep) if k]
        dates_list = [d for d, k in zip(dates_list, keep) if k]
    return vendor, sigs_list, dates_list


def _score_primary_from_signals(
    vendor: str,
    sigs: List[str],
    dates: List,
    sec_rets: pd.Series,
    *,
    mode: str = 'D',
    grace_days: Optional[int] = None,
) -> Optional[Dict]:
    """Phase 2B-2B: score pre-decoded signals against ``sec_rets`` in
    either direct (``mode='D'``) or inverse (``mode='I'``) mode.

    For ``mode='I'`` the signals are flipped Buy<->Short before
    alignment so the resulting metrics are real inverse-mode scores
    (correct Sharpe RFR sign, correct trigger mask, etc.). Any value
    other than 'D' / 'I' raises ``ValueError``.
    """
    if mode not in ('D', 'I'):
        raise ValueError(f"_score_primary_from_signals: mode must be 'D' or 'I', got {mode!r}")
    use_sigs = sigs if mode == 'D' else _flip_signals(sigs)
    # Phase 2B-2A: derive an explicit signal-state trigger mask from
    # the same alignment used to build captures, then pass it to
    # metrics_from_captures so zero-return Buy/Short days count as
    # losses (spec §15 / ledger Entry 4).
    caps, trigger_mask = apply_signals_to_secondary(
        use_sigs, dates, sec_rets, return_mask=True, grace_days=grace_days,
    )
    m = metrics_from_captures(caps, trigger_mask=trigger_mask)
    if not m:
        return None
    if m['Trigger Days'] <= 100:
        suffix = '' if mode == 'D' else f' (mode={mode})'
        print(f"[WARN] {vendor} has only {m['Trigger Days']} trigger days against secondary{suffix}")
    m['Primary Ticker'] = vendor
    return m


def _score_primary(
    primary: str,
    sec_rets: pd.Series,
    *,
    mode: str = 'D',
    grace_days: Optional[int] = None,
    data_available_through: Optional[pd.Timestamp] = None,
) -> Optional[Dict]:
    """Phase 2B-2B: load + score a single primary in either direct or
    inverse mode. ``mode='D'`` (default) preserves prior behavior.
    ``mode='I'`` flips signals Buy<->Short before scoring so the
    resulting metrics are real inverse-mode scores rather than a
    negate-and-view of direct metrics.

    Callers that need both modes for the same primary should prefer
    ``_load_primary_signals`` + two ``_score_primary_from_signals``
    calls to avoid duplicate IO.

    Phase 5C-2c: ``data_available_through`` (optional) filters the
    library to ``date <= cutoff`` before scoring. Default ``None``
    preserves byte-identical behavior.
    """
    if mode not in ('D', 'I'):
        raise ValueError(f"_score_primary: mode must be 'D' or 'I', got {mode!r}")
    vendor, sigs, dates = _load_primary_signals(
        primary, data_available_through=data_available_through,
    )
    if sigs is None:
        if mode == 'D':
            print(f"[WARN] No signal library found for {vendor}")
        return None
    return _score_primary_from_signals(
        vendor, sigs, dates, sec_rets, mode=mode, grace_days=grace_days,
    )


def _score_primary_both_modes(
    primary: str,
    sec_rets: pd.Series,
    *,
    grace_days: Optional[int] = None,
    data_available_through: Optional[pd.Timestamp] = None,
) -> Tuple[Optional[Dict], Optional[Dict]]:
    """Phase 2B-2B: load a primary once and score it in both modes.

    Returns ``(direct_metrics_or_None, inverse_metrics_or_None)``.
    Used by ``phase2_rank_all`` so a single library load services
    both ``rank_direct`` (direct) and ``rank_inverse`` (real
    inverse-mode) construction.

    Phase 5C-2c: ``data_available_through`` (optional) filters the
    library to ``date <= cutoff`` before scoring.
    """
    vendor, sigs, dates = _load_primary_signals(
        primary, data_available_through=data_available_through,
    )
    if sigs is None:
        print(f"[WARN] No signal library found for {vendor}")
        return None, None
    direct = _score_primary_from_signals(
        vendor, sigs, dates, sec_rets, mode='D', grace_days=grace_days,
    )
    inverse = _score_primary_from_signals(
        vendor, sigs, dates, sec_rets, mode='I', grace_days=grace_days,
    )
    return direct, inverse

def _stack_candidate_identity(path) -> str:
    """Phase 6I-73 amendment: deterministic, NON-METRIC identity for a
    stack-candidate path used as a tiebreaker in K=1 / K>=2 selection.

    Members are normalized to uppercase ``"<ticker>[<mode>]"`` strings
    and sorted alphabetically. The result is a single deterministic
    string so the comparison surface is unambiguous and contains no
    Sharpe / p-Value influence.
    """
    if not path:
        return ""
    members: list[str] = []
    for entry in path:
        # Each entry is (ticker, mode, sig_pair) per phase3 conventions.
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            t = str(entry[0]).upper()
            m = str(entry[1]).upper()
            members.append(f"{t}[{m}]")
        else:
            members.append(str(entry).upper())
    return ",".join(sorted(members))


def _is_better_total_capture_candidate(
    candidate_total: Optional[float],
    candidate_identity: Optional[str],
    best_total: Optional[float],
    best_identity: Optional[str],
) -> bool:
    """Phase 6I-73 amendment: explicit selection rule for candidate
    comparison. Returns ``True`` if ``candidate`` should replace
    ``best``:

      * higher ``candidate_total`` wins, or
      * equal total + lexicographically smaller ``candidate_identity``
        wins.

    Sharpe / p-Value MUST NOT be passed to this helper. Numeric ties
    on ``candidate_total`` resolve by ``candidate_identity`` ASC.
    ``best_total=None`` / ``best_identity=None`` means "no current
    best yet" so the candidate wins by default.
    """
    if best_total is None or best_identity is None:
        return candidate_total is not None
    if candidate_total is None:
        return False
    if float(candidate_total) > float(best_total):
        return True
    if float(candidate_total) == float(best_total):
        return str(candidate_identity) < str(best_identity)
    return False


def _build_bounded_inverse_cohort(
    rank_direct: pd.DataFrame,
    bottom_n: int,
) -> pd.DataFrame:
    """Phase 6I-73: derive a bounded inverse candidate cohort frame
    from the most-negative Total Capture rows of ``rank_direct``.

    Bottom_n inverse cohort display rows carry:
      * ``Mode`` = ``'I'`` (added later by phase3 cohort assembly)
      * ``Total Capture (%)`` = absolute value of the row's
        original (negative) direct Total Capture, since under
        inverse-mode that magnitude becomes a positive candidate.
      * ``Avg Daily Capture (%)`` = sign-flipped where present.
      * ``Trigger Days`` = copied from XLSX / direct row if present.
      * ``Sharpe Ratio`` = ``NaN`` (intentionally not computed here;
        K>=2 stack rows compute combined Sharpe from combined signal
        series in phase3, and the K=1 leaderboard row picks up
        accurate Sharpe via ``_combined_metrics_signals``).
      * ``p-Value`` = ``NaN`` for the same reason.

    This is the documented Phase 6I-73 bound: at most one
    ``_score_primary_from_signals(..., mode='I')`` call per build,
    only when an inverse candidate becomes the K=1 winner and an
    accurate inverse rescore is needed for that one primary.
    ``rank_inverse`` is NOT persisted as an output artifact under
    this bound.
    """
    if rank_direct is None or rank_direct.empty or bottom_n <= 0:
        return pd.DataFrame(columns=rank_direct.columns if rank_direct is not None else [])
    if 'Total Capture (%)' not in rank_direct.columns:
        return pd.DataFrame(columns=rank_direct.columns)
    # Most-negative Total Capture rows = best inverse-mode candidates.
    inv = rank_direct.sort_values(
        by='Total Capture (%)', ascending=True
    ).head(bottom_n).copy()
    if inv.empty:
        return pd.DataFrame(columns=rank_direct.columns)
    # Sign-flip the capture magnitudes so they read as positive
    # inverse-candidate Total Capture / Avg Daily Capture.
    if 'Total Capture (%)' in inv.columns:
        inv['Total Capture (%)'] = inv['Total Capture (%)'].astype(float) * -1.0
    if 'Avg Daily Capture (%)' in inv.columns:
        inv['Avg Daily Capture (%)'] = inv['Avg Daily Capture (%)'].astype(float) * -1.0
    # Sharpe / p-Value are intentionally not computed here. Phase3
    # picks up accurate metrics via _combined_metrics_signals.
    if 'Sharpe Ratio' in inv.columns:
        inv['Sharpe Ratio'] = float('nan')
    if 'p-Value' in inv.columns:
        inv['p-Value'] = float('nan')
    inv = inv.sort_values(
        by='Total Capture (%)', ascending=False
    ).reset_index(drop=True)
    return inv


def phase2_rank_all(args, primaries_df: pd.DataFrame, sec_rets: pd.Series, outdir: str, secondary: Optional[str] = None, progress_path: Optional[str] = None, *, grace_days: Optional[int] = None, data_available_through: Optional[pd.Timestamp] = None):
    """Phase 2: Rank all primaries against secondary with progress tracking.

    Phase 2B-2B: ``grace_days`` is now an explicit kwarg threaded
    through to ``_score_primary`` and from there to
    ``apply_signals_to_secondary``. ``None`` falls back to
    ``DEFAULT_GRACE_DAYS``.

    Phase 5C-2c: ``data_available_through`` (optional, default
    ``None``) filters every primary's signal library to
    ``date <= cutoff`` before scoring. The ImpactSearch XLSX
    fast-path is skipped under a non-None cutoff (validation MUST
    recompute from cutoff-filtered libraries; full-history XLSX
    metrics would leak future information into fold selection).
    Default ``None`` preserves byte-identical production behavior.
    """
    # Fast-path: use ImpactSearch .xlsx if requested and available
    strict_manifests = bool(getattr(args, "strict_manifests", False))
    if getattr(args, "prefer_impact_xlsx", False) and data_available_through is None:
        sec = (secondary or getattr(args, "secondary", "") or "").upper()
        # Capture structured rejection info so a stale-rejection produces an
        # accurate error below instead of a misleading "No ImpactSearch Excel
        # found" message.
        xlsx_rejection: Dict[str, Any] = {}
        rank_all = try_load_rank_from_impact_xlsx(
            sec=sec,
            dirpath=getattr(args, "impact_xlsx_dir", DEFAULT_IMPACT_XLSX_DIR),
            max_age_days=int(getattr(args, "impact_xlsx_max_age_days", 45)),
            strict_manifests=strict_manifests,
            rejection_out=xlsx_rejection,
        )
        # Phase 3B-2B: under --strict-manifests, a fast-path rejection
        # with NO user-provided primaries is a fatal error -- there is
        # no slow-path cohort to fall back to. Caller-provided primaries
        # let the slow path recompute.
        if (
            rank_all is None
            and strict_manifests
            and (primaries_df is None or len(primaries_df) == 0)
        ):
            raise SystemExit(
                f"[FATAL] --strict-manifests rejected the ImpactSearch "
                f"XLSX fast-path for {sec}, and no primary tickers were "
                f"provided for slow-path recomputation. Provide primaries, "
                f"repair/regenerate the XLSX manifest, or rerun without "
                f"--strict-manifests."
            )
        if isinstance(rank_all, pd.DataFrame) and len(rank_all):
            # If UI primaries provided, filter Excel to that cohort before any ranking
            if primaries_df is not None and not primaries_df.empty:
                allow = set(primaries_df['Primary Ticker'].astype(str).str.upper())
                before = len(rank_all)
                rank_all = rank_all[rank_all['Primary Ticker'].astype(str).str.upper().isin(allow)].reset_index(drop=True)
                print(f"[PHASE2] ImpactSearch XLSX filtered to user primaries: {len(rank_all)}/{before} rows kept")
                if rank_all.empty:
                    raise SystemExit(f"[FATAL] None of the entered primaries are present in the ImpactSearch Excel for {secondary}.")

            rank_direct = rank_all.sort_values(by='Total Capture (%)', ascending=False).reset_index(drop=True)
            # Phase 6I-73: the prior xlsx fast-path full-universe inverse
            # rescore loop is removed. The bottom_n inverse cohort is
            # now derived directly from the most-negative Total Capture
            # rows of rank_direct (no per-primary
            # _score_primary_from_signals call in phase2). Bottom_n
            # inverse candidate display rows carry the *positive*
            # inverse-candidate Total Capture (sign-flipped from the
            # original negative direct row), and Sharpe / p-Value are
            # NaN at the cohort level — accurate K=1 metrics are
            # computed in phase3 via _combined_metrics_signals on the
            # aligned inverse signals, and K>=2 stack rows compute
            # accurate combined Sharpe from combined signal series.
            requested_bottom_n = int(getattr(args, 'bottom_n', 0) or 0)
            rank_inverse = _build_bounded_inverse_cohort(
                rank_direct, requested_bottom_n,
            )

            write_table(rank_all, os.path.join(outdir, 'rank_all'))
            write_table(rank_direct, os.path.join(outdir, 'rank_direct'))
            # Phase 6I-73: rank_inverse is NOT written to disk.
            # Emit progress counters even on XLSX fast-path so UI shows advancement
            if progress_path:
                total = int(len(rank_all))
                _write_progress(
                    progress_path,
                    status='running',
                    phase='ranking',
                    percent=59.0,
                    message=f'Loaded ImpactSearch ranking: {total} rows.',
                    counters={'primaries_done': total, 'primaries_total': total},
                    outdir=outdir,
                    secondary=secondary
                )
            return rank_all, rank_direct, rank_inverse
        else:
            # Phase 3B-2B: under --strict-manifests with caller-provided
            # primaries, fall through to the slow path so the run can
            # recompute against the exact primaries cohort. The 70K-
            # primary guard below still applies when no primaries were
            # provided at all -- that case raises SystemExit above.
            if (
                strict_manifests
                and primaries_df is not None
                and len(primaries_df) > 0
            ):
                print(
                    f"[FASTPATH] --strict-manifests rejected the ImpactSearch "
                    f"XLSX for {sec}; falling through to slow path with "
                    f"{len(primaries_df)} caller-provided primaries."
                )
            else:
                # Do NOT compute 70k+ primaries if user asked for fast-path.
                # Distinguish "found but rejected as stale" from "not found"
                # so the user gets actionable guidance rather than chasing a
                # missing file that actually exists but is too old.
                if xlsx_rejection.get('reason') == 'stale':
                    raise RuntimeError(
                        f"ImpactSearch Excel found for secondary "
                        f"'{secondary or args.secondary}' but rejected as stale:\n"
                        f"  {xlsx_rejection['path']}\n"
                        f"  age={xlsx_rejection['age_days']:.0f}d > "
                        f"max_age_days={xlsx_rejection['max_age_days']}.\n"
                        f"Refresh the workbook, raise "
                        f"--impact-xlsx-max-age-days, or disable Use "
                        f"ImpactSearch .xlsx."
                    )
                raise RuntimeError(f"No ImpactSearch Excel found for secondary '{secondary or args.secondary}' in "
                                   f"{getattr(args,'impact_xlsx_dir', DEFAULT_IMPACT_XLSX_DIR)}. "
                                   f"Expected a file like '{secondary or args.secondary}_analysis.xlsx'. "
                                   f"Uncheck 'Use ImpactSearch .xlsx' to compute from signal libraries.")
    max_workers = None if args.threads == 'auto' else int(args.threads)
    rows_direct: List[Dict] = []
    # Phase 6I-73: rows_inverse retired — direct-only scoring; inverse
    # cohort derived later via _build_bounded_inverse_cohort.
    missing: List[str] = []
    total = len(primaries_df['Primary Ticker'])

    if VERBOSE:
        print(f"[PHASE2] Scoring {total} primary tickers against {secondary}...")

    # Phase 2B-2B: score both modes per primary from a single library
    # load. rank_inverse is now built from real inverse-mode scores
    # (signals flipped Buy<->Short before alignment) rather than a
    # negate-and-view of direct metrics. The negate-and-view produces
    # an incorrect Sharpe because the risk-free-rate term doesn't
    # change sign under metric negation; flipping signals first and
    # rescoring resolves that asymmetry.
    # Phase 6I-73: score primaries in DIRECT mode only. The bottom_n
    # inverse cohort is derived from the most-negative direct Total
    # Capture rows via _build_bounded_inverse_cohort. This eliminates
    # the prior full-universe inverse rescore loop. K=1 inverse-winner
    # metrics still arrive accurately through phase3's
    # _combined_metrics_signals on aligned inverse signals; K>=2 stack
    # metrics still come from combined signal series.
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        # Phase 3B-2A amendment: wrap each submit with copy_context so the
        # per-run input-manifest collector ContextVar set by
        # run_for_secondary flows into the worker thread.
        def _direct_score(_t, _sec_rets):
            return _score_primary(
                _t, _sec_rets, mode='D',
                grace_days=grace_days,
                data_available_through=data_available_through,
            )
        futures = {
            _submit_with_context(
                ex, _direct_score, t, sec_rets,
            ): t
            for t in primaries_df['Primary Ticker']
        }

        # Use tqdm if available, otherwise simple counter
        if tqdm and not getattr(args, 'no_progress', False):
            progress = tqdm(as_completed(futures), total=total, desc="Ranking primaries")
            iterator = progress
        else:
            iterator = as_completed(futures)

        # Smoother UI: update at least every 50 items or 0.5% or every 2s
        last_update_ts = time.time()
        step = max(50, max(1, total // 200))
        for i, fut in enumerate(iterator, 1):
            ticker = futures[fut]
            direct_m = fut.result()
            if direct_m is None:
                missing.append(ticker)
            else:
                rows_direct.append(direct_m)

            # Progress reporting with counters
            now = time.time()
            if progress_path and (i % step == 0 or (now - last_update_ts) >= 2.0 or i == total):
                pct = 25.0 + (35.0 * i / total)  # Phase 2 runs from 25% to 60%
                _write_progress(
                    progress_path,
                    status='running',
                    phase='ranking',
                    percent=pct,
                    message=f'Ranking primaries {i}/{total}.',
                    counters={'primaries_done': int(i), 'primaries_total': int(total)},
                    outdir=outdir,
                    secondary=secondary
                )
                last_update_ts = now
            # Simple progress without tqdm
            elif not tqdm and i % step == 0:
                print(f"[PROGRESS] Phase 2: {i}/{total} primaries processed ({100*i//total}%)")

    if missing:
        print(f"[INFO] {len(missing)} tickers had no valid metrics or missing signal libraries")

    if not rows_direct:
        raise SystemExit("[FATAL] No primaries produced valid metrics.")

    # rank_all and rank_direct: direct-mode metrics. Phase 6I-73:
    # rank_inverse is a bounded in-memory cohort derived from the
    # most-negative direct Total Capture rows; it is NOT persisted
    # to disk. Sharpe / p-Value on the inverse cohort are NaN at the
    # cohort layer (see _build_bounded_inverse_cohort docstring).
    rank_all = pd.DataFrame(rows_direct)
    rank_direct = rank_all.sort_values(by='Total Capture (%)', ascending=False).reset_index(drop=True)
    requested_bottom_n = int(getattr(args, 'bottom_n', 0) or 0)
    rank_inverse = _build_bounded_inverse_cohort(rank_direct, requested_bottom_n)

    write_table(rank_all, os.path.join(outdir, 'rank_all'))
    write_table(rank_direct, os.path.join(outdir, 'rank_direct'))
    # Phase 6I-73: rank_inverse is NOT written to disk.
    return rank_all, rank_direct, rank_inverse

# ---------- Phase 3: Stack Builder ----------
def _captures_for(primary: str, mode: str, sec_rets: pd.Series, *, grace_days: Optional[int] = None) -> pd.Series:
    vendor, _ = resolve_symbol(primary)
    lib = load_lib_or_none(vendor)
    if not lib:
        return pd.Series(dtype=FLOAT_DTYPE)
    sigs = lib.get('primary_signals') or lib.get('primary_signals_int8')
    dates = lib.get('dates') or lib.get('date_index')
    if not sigs or not dates:
        return pd.Series(dtype=FLOAT_DTYPE)
    if isinstance(sigs[0], (int, np.integer)):
        dec = {1:'Buy', -1:'Short', 0:'None'}
        sigs = [dec.get(int(x), 'None') for x in sigs]
    caps = apply_signals_to_secondary(sigs, dates, sec_rets, grace_days=grace_days)
    return (-caps if mode == 'I' else caps).astype(FLOAT_DTYPE)

def _combined_metrics(member_caps: List[pd.Series]) -> Tuple[pd.Series, Optional[Dict]]:
    if not member_caps:
        return pd.Series(dtype=FLOAT_DTYPE), None
    aligned = pd.concat(member_caps, axis=1).fillna(0.0)
    # Intersection of triggers to match impactsearch/spymaster counting tighter:
    # day counts only if *every* member triggered (non-zero) that day.
    if COMBINE_INTERSECTION:
        mask = aligned.ne(0.0).all(axis=1)
        aligned = aligned[mask]
        if VERBOSE:
            orig_days = len(aligned.index)
            filtered_days = mask.sum() if hasattr(mask, 'sum') else len(aligned)
            print(f"[COMBINE] Intersection mode: {filtered_days}/{orig_days} days with all members triggering")
    combined = aligned.mean(axis=1).astype(FLOAT_DTYPE)
    m = metrics_from_captures(combined)
    return combined, m

# NEW: signal-level helpers with strict-calendar mask (spymaster-parity)
def _signals_aligned_and_mask(primary: str, mode: str, sec_index: pd.DatetimeIndex, *, grace_days: Optional[int] = None, data_available_through: Optional[pd.Timestamp] = None) -> Tuple[pd.Series, pd.Series]:
    """Return (signals_aligned_to_sec_index, present_mask_before_fill).

    Phase 2B-2B: ``grace_days`` is now an explicit kwarg. ``None``
    falls back to ``DEFAULT_GRACE_DAYS``; explicit ints (including 0)
    are honored verbatim. Calendar policy stays unified with Phase 2's
    ``apply_signals_to_secondary`` (Entry 5).

    Phase 5C-2c: ``data_available_through`` (optional) filters the
    raw signal library to ``date <= cutoff`` BEFORE alignment, so
    validation folds never observe signals dated after the
    selection / evaluation cutoff. Default ``None`` preserves
    byte-identical production behavior.
    """
    vendor, _ = resolve_symbol(primary)
    try:
        lib = load_lib_or_none(vendor)
        if not lib:
            return pd.Series('None', index=sec_index), pd.Series(False, index=sec_index)
        sigs = lib.get('primary_signals') or lib.get('primary_signals_int8')
        dates = lib.get('dates') or lib.get('date_index')
        if sigs is None or dates is None:
            return pd.Series('None', index=sec_index), pd.Series(False, index=sec_index)

        # Validate length match
        if len(sigs) != len(dates):
            print(f"[WARN] {vendor}: Signal library corrupted - {len(sigs)} signals vs {len(dates)} dates (skipping)")
            return pd.Series('None', index=sec_index), pd.Series(False, index=sec_index)

        if len(sigs) > 0 and isinstance(sigs[0], (int, np.integer)):
            sigs = [{1:'Buy', -1:'Short', 0:'None'}.get(int(x), 'None') for x in sigs]
        raw = pd.Series(list(sigs), index=pd.to_datetime(dates))
        if getattr(raw.index, "tz", None) is not None:
            raw.index = raw.index.tz_localize(None)
        if data_available_through is not None:
            cutoff_ts = pd.Timestamp(data_available_through)
            raw = raw.loc[raw.index <= cutoff_ts]
    except Exception as e:
        print(f"[ERROR] Failed to load signals for {vendor}: {e}")
        raise RuntimeError(f"Ticker {vendor} signal library error: {e}") from e
    # Unify calendar policy with Phase 2 (apply_signals_to_secondary) per
    # Phase 1B Intentional Delta Ledger Entry 5 (StackBuilder Phase 2 vs
    # Phase 3 scoring divergence). Both phases now resolve grace via the
    # same ``_effective_grace_days`` helper (Entry 7 amendment, 2B-2B).
    grace_days = _effective_grace_days(grace_days)
    if grace_days > 0:
        aligned = raw.reindex(sec_index, method='pad', tolerance=pd.Timedelta(days=grace_days))
    else:
        aligned = raw.reindex(sec_index, method=None)
    present = aligned.notna()
    s = aligned.fillna('None').astype(str).str.strip()
    if mode == 'I':
        s = s.replace({'Buy':'Short','Short':'Buy'})
    return s, present

def _combine_signals(members: List[pd.Series]) -> pd.Series:
    """Allow Buy/NONE or Short/NONE; cancel to NONE on any Buy+Short mix.
    Delegates to canonical_scoring.combine_consensus_signals (spec §18).
    """
    if not members:
        return pd.Series(dtype=object)
    df = pd.concat(members, axis=1).fillna('None').astype(str)
    return _canonical_consensus([df[c] for c in df.columns])

def _captures_from_signals(signals: pd.Series, sec_rets: pd.Series) -> pd.Series:
    sig = signals.reindex(sec_rets.index).fillna('None').astype(str)
    cap = pd.Series(0.0, index=sec_rets.index, dtype=FLOAT_DTYPE)
    buy = sig.eq('Buy'); short = sig.eq('Short')
    cap.loc[buy] = sec_rets.loc[buy]
    cap.loc[short] = -sec_rets.loc[short]
    return cap

def _combined_metrics_signals(member_signals: List[Union[pd.Series, Tuple[pd.Series, pd.Series]]],
                              sec_rets: pd.Series) -> Tuple[pd.Series, Optional[Dict]]:
    if not member_signals:
        return pd.Series(dtype=FLOAT_DTYPE), None
    # Unpack series and strict-calendar masks
    series_list, masks = [], []
    for item in member_signals:
        if isinstance(item, tuple) and len(item) == 2:
            s, m = item
        else:
            s, m = item, pd.Series(True, index=getattr(item, 'index', sec_rets.index))
        series_list.append(s)
        masks.append(m.reindex(s.index).fillna(False))
    # Combine signals then zero out any day where any member lacked a native observation
    comb_sig = _combine_signals(series_list)
    present_all = masks[0].copy()
    for m in masks[1:]:
        present_all &= m
    comb_sig = comb_sig.where(present_all, 'None')
    combined_caps = _captures_from_signals(comb_sig, sec_rets)
    # Spec §15 / ledger Entry 4: trigger days are signal-state based.
    trigger_mask = comb_sig.isin(['Buy', 'Short'])
    m = metrics_from_captures(combined_caps, trigger_mask=trigger_mask)
    return combined_caps, m

def phase3_build_stacks(args, rank_direct: pd.DataFrame, rank_inverse: pd.DataFrame, sec_rets: pd.Series, outdir: str, progress_cb=None, *, grace_days: Optional[int] = None, data_available_through: Optional[pd.Timestamp] = None, validation_collector: Optional[Callable[[dict], None]] = None) -> Tuple[pd.DataFrame, List]:
    """Beam+exhaustive search over top/bottom cohort with both modes.
    progress_cb: optional callback for reporting progress
    Returns: (leaderboard_df, final_members_list)

    Phase 2B-2B: ``grace_days`` is now an explicit kwarg threaded
    through to ``_signals_aligned_and_mask`` for every (ticker, mode)
    in the cohort. ``None`` falls back to ``DEFAULT_GRACE_DAYS``.

    Phase 5C-2c: ``data_available_through`` (optional) filters every
    signal library to ``date <= cutoff`` before alignment so the
    fold's ``selection_cutoff`` is honored. ``validation_collector``
    (optional callable) receives one record per uniquely-canonical
    stack definition that was successfully scored, including
    candidates later rejected by min-trigger or monotonic-improvement
    gates. Both default to ``None`` and preserve byte-identical
    production behavior. Leaderboard, pruning, search_stats, and
    output files are unchanged when ``validation_collector is None``.
    """
    # Phase 5C-2c: per-fold canonical de-dup + emission counter for the
    # optional validation collector. State is local to this call so
    # production runs (collector=None) pay zero overhead and concurrent
    # validation folds never share state.
    _collector_seen: set = set()
    _collector_state = {"rank": 0}

    def _emit_validation_record(
        path,
        k_value,
        search_source,
        in_sample_metrics,
        rejected_reason,
    ):
        if validation_collector is None:
            return
        if in_sample_metrics is None:
            return
        try:
            canon = tuple(
                sorted(
                    (str(t), str(m))
                    for (t, m, _sig) in path
                )
            )
        except Exception:
            return
        if canon in _collector_seen:
            return
        _collector_seen.add(canon)
        _collector_state["rank"] += 1
        try:
            metrics_view = {
                kk: vv for kk, vv in in_sample_metrics.items()
                if not kk.startswith("_")
            }
        except Exception:
            metrics_view = {}
        record = {
            "members": canon,
            "k": int(k_value),
            "fold_train_rank": int(_collector_state["rank"]),
            "search_source": str(search_source),
            "in_sample_metrics": metrics_view,
            "in_sample_rejected_reason": rejected_reason,
        }
        try:
            validation_collector(record)
        except Exception:
            # Collector exceptions must NEVER taint the production
            # leaderboard; swallow and continue.
            pass

    topN, botN = int(args.top_n), int(args.bottom_n)
    min_td = int(getattr(args, 'min_trigger_days', 30))
    eps = float(getattr(args, 'sharpe_eps', 1e-6))
    # Phase 6I-73: Total Capture is the only supported selection metric.
    # Legacy 'sharpe' values from older configs are normalized to
    # 'total_capture' here so old run_manifest replays still work.
    seed_by = getattr(args, 'seed_by', 'total_capture')
    if seed_by == 'sharpe':
        seed_by = 'total_capture'
    optimize_by = getattr(args, 'optimize_by', None) or seed_by
    if optimize_by == 'sharpe':
        optimize_by = 'total_capture'
    search = getattr(args, 'search', 'beam')
    beam_w = int(getattr(args, 'beam_width', 12))
    ex_k = int(getattr(args, 'exhaustive_k', 3))
    both_modes = bool(getattr(args, 'both_modes', False))  # Changed default to False
    k_patience = int(getattr(args, 'k_patience', 0))
    allow_decreasing = bool(getattr(args, 'allow_decreasing', False))

    # 1) Build cohort. Always include topN (as Direct) and bottomN (as Inverse).
    top = rank_direct.head(topN).copy()
    top['Mode'] = 'D'
    bottom = rank_inverse.head(botN).copy()
    bottom['Mode'] = 'I'
    cohort0 = pd.concat([top, bottom], ignore_index=True)
    cohort0 = cohort0[['Primary Ticker','Mode','Total Capture (%)','Sharpe Ratio','p-Value']]

    # Auto-enable both modes when duplicates exist to prevent mode collapse
    both_modes_eff = bool(both_modes or cohort0.duplicated(subset=['Primary Ticker']).any())
    if both_modes_eff and not both_modes:
        print(f"[PHASE3] Auto-enabled both_modes: {cohort0.duplicated(subset=['Primary Ticker']).sum()} ticker(s) appear in both Top and Bottom")

    # Optionally duplicate both modes for every unique ticker
    if both_modes_eff:
        uniq = sorted(set(cohort0['Primary Ticker']))
        extra = []
        for t in uniq:
            extra.append({'Primary Ticker': t, 'Mode':'D'})
            extra.append({'Primary Ticker': t, 'Mode':'I'})
        cohort = pd.DataFrame(extra).drop_duplicates()
    else:
        cohort = cohort0[['Primary Ticker','Mode']].drop_duplicates()

    write_table(cohort, os.path.join(outdir, 'cohort'))

    # 2) Precompute SIGNALS(+mask) for every (ticker,mode)
    sig_cache: Dict[Tuple[str,str], Tuple[pd.Series, pd.Series]] = {}
    for _, r in cohort.iterrows():
        t, m = r['Primary Ticker'], r['Mode']
        if (t, m) not in sig_cache:
            sig_cache[(t, m)] = _signals_aligned_and_mask(t, m, sec_rets.index, grace_days=grace_days, data_available_through=data_available_through)
    def sigs_for(t, m): return sig_cache.get((t, m), (pd.Series('None', index=sec_rets.index),
                                                        pd.Series(False, index=sec_rets.index)))

    # 3) Build singles (K=1) that pass min trigger days
    singles = []
    for _, r in cohort.iterrows():
        t, m = r['Primary Ticker'], r['Mode']
        sig_pair = sigs_for(t, m)
        _, met = _combined_metrics_signals([sig_pair], sec_rets)
        if not met:
            continue
        # add raw fields for stable sorting
        met['Sharpe_raw'] = float(met['Sharpe Ratio'])
        met['Total_raw']  = float(met['Total Capture (%)'])
        met['p_raw']      = (float(met['p-Value']) if met['p-Value'] != 'N/A' else None)
        if int(met['Trigger Days']) >= min_td:
            singles.append(((t, m, sig_pair), met))
            _emit_validation_record(
                [(t, m, sig_pair)], 1, "single", met, None,
            )
        else:
            _emit_validation_record(
                [(t, m, sig_pair)], 1, "single", met, "trigger_days",
            )
    if not singles:
        raise SystemExit("[FATAL] No single candidate passed the min Trigger Days gate.")

    # Phase 6I-73 amendment: K=1 selection must use Total Capture
    # only, with a NON-METRIC deterministic tiebreaker. Sharpe and
    # p-Value are display-only and MUST NOT influence selection
    # anywhere — including the tie path. Tiebreaker order:
    #   (1) higher Total Capture wins
    #   (2) lexicographically smaller (ticker_norm, mode_norm) wins
    singles.sort(
        key=lambda it: (
            -float(it[1]['Total_raw']),
            str(it[0][0]).upper(),
            str(it[0][1]).upper(),
        )
    )

    # Helper to score any list of (t,mode,sig)
    def score_path(path):
        # Extract signal pairs from path
        signal_pairs = [x[2] if isinstance(x[2], tuple) else (x[2], pd.Series(True, index=sec_rets.index))
                        for x in path]
        comb, met = _combined_metrics_signals(signal_pairs, sec_rets)
        if not met:
            return None, None
        met['Sharpe_raw'] = float(met['Sharpe Ratio'])
        met['Total_raw']  = float(met['Total Capture (%)'])
        met['p_raw']      = (float(met['p-Value']) if met['p-Value'] != 'N/A' else None)
        return comb, met

    # 4) Leaderboard and search state
    best1 = [singles[0][0]]
    comb, met = score_path(best1)
    print(f"[PHASE3] K=1 Seed: {best1[0][0]}[{best1[0][1]}] | Sharpe={met['Sharpe Ratio']:.3f} | TD={met['Trigger Days']} | Capture={met['Total Capture (%)']:.2f}%")
    leaderboard = [{
        'K': 1,
        'Trigger Days': met['Trigger Days'],
        'Total Capture (%)': met['Total Capture (%)'],
        'Sharpe Ratio': met['Sharpe Ratio'],
        'p-Value': met['p-Value'],
        'Members': [f"{best1[0][0]}[{best1[0][1]}]"]
    }]
    write_json(os.path.join(outdir, 'combo_k=1.json'), leaderboard[0])

    # Beam = list of tuples: (path, comb, met)
    beam = [(best1, comb, met)]

    # Pre-compute total combos for ETA calculation
    import math
    def comb_count(n, k):
        if n < k or k < 0:
            return 0
        return math.factorial(n) // (math.factorial(k) * math.factorial(n - k))

    uniq_tickers = sorted({t for (t, _) in cohort.itertuples(index=False)})
    n_candidates = len(uniq_tickers)
    max_k = int(args.max_k)
    ex_k_limit = min(ex_k, max_k)
    combos_total_all = sum(comb_count(n_candidates, k) for k in range(2, ex_k_limit + 1)) if n_candidates >= 2 else 0
    combos_tested_acc = 0

    if progress_cb and combos_total_all:
        progress_cb(f"Stacking prep: {n_candidates} candidates, {combos_total_all:,} total combos",
                    counters={'combos_tested': 0, 'combos_total': combos_total_all, 'current_k': 1})

    # Exhaustive for small K
    def exhaustive_k(K):
        nonlocal combos_tested_acc
        uniq_tickers = sorted({t for (t, _) in cohort.itertuples(index=False)})
        # enforce ticker uniqueness in stacks
        cand_tickers = [t for t in uniq_tickers]
        best = None
        # Track progress emissions
        last_emit_time = time.time()
        k_combos_tested = 0
        for tickers in combinations(cand_tickers, K):
            # for both_modes_eff, consider all 2^K mode assignments; else derive mode from cohort0
            # Generate mode combinations
            if both_modes_eff:
                # When both_modes is enabled, test all 2^K combinations
                mode_sets = product(['D','I'], repeat=K)
            else:
                # Use the mode from cohort0 if available, else 'D'
                cohort0_dict = cohort0.set_index('Primary Ticker')['Mode'].to_dict()
                mode_sets = [tuple(cohort0_dict.get(t, 'D') for t in tickers)]
            for modes in mode_sets:
                path = []
                valid = True
                for t, m in zip(tickers, modes):
                    if (t, m) not in sig_cache:
                        valid = False; break
                    sig_pair = sigs_for(t, m)
                    path.append((t, m, sig_pair))
                if not valid:
                    search_stats['combinations_rejected'] += 1
                    search_stats['rejection_reasons']['invalid'] += 1
                    continue
                search_stats['combinations_tested'] += 1
                k_combos_tested += 1
                # Emit progress every ~1.5s
                now = time.time()
                if progress_cb and (now - last_emit_time) >= 1.5:
                    last_emit_time = now
                    tested_all = int(combos_tested_acc + k_combos_tested)
                    progress_cb(f"Building stack K={K} — {tested_all:,}/{combos_total_all:,} combos",
                                counters={'combos_tested': tested_all, 'combos_total': combos_total_all, 'current_k': K})
                comb, mm = score_path(path)
                if not mm:
                    search_stats['combinations_rejected'] += 1
                    search_stats['rejection_reasons']['invalid'] += 1
                    continue
                if int(mm['Trigger Days']) < min_td:
                    search_stats['combinations_rejected'] += 1
                    search_stats['rejection_reasons']['trigger_days'] += 1
                    if VERBOSE:
                        print(f"  [REJECT] {[t for t in tickers]}: Trigger Days {mm['Trigger Days']} < {min_td}")
                    _emit_validation_record(path, K, "exhaustive", mm, "trigger_days")
                    continue
                # Phase 6I-73 amendment: Total Capture is the only
                # selection criterion. The legacy 'optimize_by ==
                # sharpe' branch is removed entirely; the only
                # monotone-improvement check is on Total Capture.
                if not allow_decreasing:
                    prev_metric = leaderboard[-1]['Total Capture (%)']
                    cur_metric = mm['Total_raw']
                    metric_name = 'Total Capture'
                    if float(cur_metric) <= float(prev_metric) + eps:
                        search_stats['combinations_rejected'] += 1
                        search_stats['rejection_reasons']['sharpe_improvement'] += 1  # reuse counter for "no improvement"
                        if VERBOSE:
                            print(f"  [REJECT] {[t for t in tickers]}: {metric_name} {cur_metric:.4f}% <= {prev_metric:.4f}% + {eps}")
                        _emit_validation_record(path, K, "exhaustive", mm, "sharpe_improvement")
                        continue
                _emit_validation_record(path, K, "exhaustive", mm, None)
                # Phase 6I-73 amendment: comparison uses Total Capture
                # only, with a NON-METRIC deterministic tiebreaker.
                # Sharpe / p-Value MUST NOT influence the choice.
                cand_total = float(mm['Total_raw'])
                cand_identity = _stack_candidate_identity(path)
                if _is_better_total_capture_candidate(
                    cand_total, cand_identity,
                    None if best is None else best[0][0],
                    None if best is None else best[0][1],
                ):
                    best = ((cand_total, cand_identity), path, comb, mm)
        # Final update for this K level
        combos_tested_acc += k_combos_tested
        if progress_cb:
            progress_cb(f"K={K} complete.",
                        counters={'combos_tested': combos_tested_acc, 'combos_total': combos_total_all, 'current_k': K})
        return best

    # Track patience for non-improving K levels
    patience_used = 0

    # Track search statistics
    search_stats = {
        'combinations_tested': 0,
        'combinations_rejected': 0,
        'rejection_reasons': {'trigger_days': 0, 'sharpe_improvement': 0, 'invalid': 0}
    }

    for K in range(2, int(args.max_k) + 1):
        if progress_cb:
            progress_cb(f"Building stack K={K}...")
        if VERBOSE:
            print(f"\n[PHASE3] Building stack K={K}, search={search}, patience_used={patience_used}/{k_patience}")

        found = None
        if search == 'exhaustive' or K <= ex_k:
            found = exhaustive_k(K)
        else:
            # Beam expand. Phase 6I-73 amendment: Total Capture is
            # the only selection metric; tiebreaker is the
            # deterministic stack identity (sorted member list).
            # Sharpe / p-Value MUST NOT influence ordering.
            cand_states = []
            seen = set()
            for path, _, prevm in beam:
                prev_metric = float(prevm['Total_raw'])
                used = {t for (t, _, _) in path}
                for _, r in cohort.iterrows():
                    t, m = r['Primary Ticker'], r['Mode']
                    if t in used:  # no duplicate tickers
                        continue
                    sig_pair = sigs_for(t, m)
                    new_path = path + [(t, m, sig_pair)]
                    search_stats['combinations_tested'] += 1
                    comb2, m2 = score_path(new_path)
                    if not m2:
                        search_stats['combinations_rejected'] += 1
                        search_stats['rejection_reasons']['invalid'] += 1
                        continue
                    if int(m2['Trigger Days']) < min_td:
                        search_stats['combinations_rejected'] += 1
                        search_stats['rejection_reasons']['trigger_days'] += 1
                        _emit_validation_record(new_path, K, "beam", m2, "trigger_days")
                        continue
                    # Enforce monotone improvement on Total Capture only.
                    if not allow_decreasing:
                        cur_metric = float(m2['Total_raw'])
                        if cur_metric <= prev_metric + eps:
                            search_stats['combinations_rejected'] += 1
                            search_stats['rejection_reasons']['sharpe_improvement'] += 1
                            _emit_validation_record(new_path, K, "beam", m2, "sharpe_improvement")
                            continue
                    _emit_validation_record(new_path, K, "beam", m2, None)
                    cand_total = float(m2['Total_raw'])
                    cand_identity = _stack_candidate_identity(new_path)
                    sig = (
                        tuple(sorted([x[0] for x in new_path])),
                        tuple([x[1] for x in new_path]),
                    )
                    if sig in seen:
                        continue
                    seen.add(sig)
                    # Sort key: negative total (descending order),
                    # then ascending identity (lexical tiebreaker).
                    cand_states.append((
                        (-cand_total, cand_identity), new_path, comb2, m2,
                    ))
            if cand_states:
                cand_states.sort(key=lambda x: x[0])  # ascending
                beam = [(p, c, m) for _, p, c, m in cand_states[:beam_w]]
                # choose best of current K: smallest sort key.
                _, best_path, best_comb, best_met = cand_states[0]
                found = (None, best_path, best_comb, best_met)

        if not found:
            if k_patience > 0 and patience_used < k_patience:
                patience_used += 1
                # Phase 6I-73 amendment: Total Capture only.
                print(f"[PHASE3] K={K}: No Total Capture improvement (>={eps:.6f}), using patience {patience_used}/{k_patience}")
                continue  # Continue to next K instead of breaking
            else:
                if allow_decreasing:
                    print(f"[PHASE3] Stopping at K={K-1}: No valid candidates with >={min_td} trigger days")
                else:
                    print(f"[PHASE3] Stopping at K={K-1}: No candidate improves Total Capture by >{eps:.6f} with >={min_td} trigger days")
                break

        # Found improvement, reset patience
        patience_used = 0

        _, best_path, best_comb, best_met = found
        leaderboard.append({
            'K': K,
            'Trigger Days': best_met['Trigger Days'],
            'Total Capture (%)': best_met['Total Capture (%)'],
            'Sharpe Ratio': best_met['Sharpe Ratio'],
            'p-Value': best_met['p-Value'],
            'Members': [f"{t}[{m}]" for (t,m,_) in best_path]
        })
        write_json(os.path.join(outdir, f'combo_k={K}.json'), leaderboard[-1])
        # Calculate Sharpe improvement
        sharpe_improvement = best_met['Sharpe Ratio'] - leaderboard[-2]['Sharpe Ratio'] if len(leaderboard) > 1 else 0
        print(f"[PHASE3] K={K}: Sharpe={best_met['Sharpe Ratio']:.3f} (+{sharpe_improvement:.4f}) | TD={best_met['Trigger Days']} | Capture={best_met['Total Capture (%)']:.2f}% | Members={leaderboard[-1]['Members']}")
        # reset beam around the best path if exhaustive; else beam already updated
        if search == 'exhaustive' or K <= ex_k:
            beam = [(best_path, best_comb, best_met)]

    ldf = pd.DataFrame(leaderboard, columns=['K','Trigger Days','Total Capture (%)','Sharpe Ratio','p-Value','Members']).infer_objects(copy=False)
    write_table(ldf, os.path.join(outdir, 'combo_leaderboard'))

    # Write search statistics
    if VERBOSE or getattr(args, 'save_stats', False):
        search_stats['final_k'] = len(leaderboard)
        search_stats['best_sharpe'] = leaderboard[-1]['Sharpe Ratio'] if leaderboard else None
        search_stats['best_capture'] = leaderboard[-1]['Total Capture (%)'] if leaderboard else None
        write_json(os.path.join(outdir, 'search_stats.json'), search_stats)
        if VERBOSE:
            print(f"\n[STATS] Search complete:")
            print(f"  Combinations tested: {search_stats['combinations_tested']}")
            print(f"  Combinations rejected: {search_stats['combinations_rejected']}")
            print(f"  Rejection reasons: {search_stats['rejection_reasons']}")

    final_members = leaderboard[-1]['Members'] if leaderboard else []
    return ldf, final_members

# ---------- Minimal Dash UI (optional) ----------
def run_dash(outdir: str, port: int = 8054):
    try:
        from dash import Dash, html, dcc, dash_table, no_update
        from dash.dependencies import Input, Output, State
        from dash.exceptions import PreventUpdate
    except Exception:
        print("[WARN] Dash not installed; skipping UI.")
        return
    import threading
    app = Dash(__name__, suppress_callback_exceptions=True)
    warn = html.Span("Warning: verify close-time parity to avoid look-ahead bias.",
                     style={'color':'#b00','fontWeight':'bold'})
    app.layout = html.Div([
        html.H3("StackBuilder"),
        html.Div(warn, style={'marginBottom':'8px'}),
        html.Div([
            html.Label("Secondary ticker(s)"),
            dcc.Input(id='secondary-input', type='text', value='', placeholder='e.g. SPY or SPY, QQQ, IWM', debounce=True),
            html.Label("Primary tickers (comma or whitespace separated)", style={'marginLeft':'12px'}),
            dcc.Textarea(id='primaries-input', placeholder='AAPL, MSFT, META ...', style={'width':'60%', 'height':'80px'}),
        ], style={'marginBottom':'8px'}),
        html.Div([
            html.Label("Top N"), dcc.Input(id='topn', type='number', value=20, min=1, step=1, style={'width':'80px', 'marginRight':'12px'}),
            html.Label("Bottom N"), dcc.Input(id='bottomn', type='number', value=20, min=1, step=1, style={'width':'80px', 'marginRight':'12px'}),
            html.Label("Max K"), dcc.Input(id='maxk', type='number', value=6, min=1, step=1, style={'width':'80px', 'marginRight':'12px'}),
            html.Label("Exhaustive K"), dcc.Input(id='exk', type='number', value=4, min=1, step=1, style={'width':'100px', 'marginRight':'12px'}),
            html.Label("alpha"), dcc.Input(id='alpha', type='number', value=0.05, min=0.0, step=0.01, style={'width':'100px', 'marginRight':'12px'}),
            html.Label("Min Trigger Days"),
            dcc.Input(id='min-trigger-days', type='number', value=30, min=1, step=1,
                      style={'width':'120px','marginRight':'12px'}),
            html.Label("Sharpe ε"),
            dcc.Input(id='sharpe-eps', type='number', value=1e-6, min=0, step=1e-6,
                      style={'width':'140px','marginRight':'12px'}),
            # Phase 6I-73: Sharpe is removed as a selection criterion.
            # Total Capture is the only supported seed/optimize metric;
            # the radio is preserved as a single-option control so the
            # Dash callback wiring keeps its existing State() shape.
            html.Label("Seed by"),
            dcc.RadioItems(
                id='seed-by',
                options=[{'label':'Total Capture','value':'total_capture'}],
                value='total_capture',
                labelStyle={'display':'inline-block','marginRight':'12px'},
                style={'display':'inline-block','marginRight':'12px'}
            ),
            html.Label("Optimize by"),
            dcc.RadioItems(
                id='optimize-by',
                options=[{'label':'Total Capture','value':'total_capture'}],
                value='total_capture',
                labelStyle={'display':'inline-block','marginRight':'12px'},
                style={'display':'inline-block','marginRight':'12px'}
            ),
            dcc.Checklist(id='allow-decreasing', options=[{'label':'Allow metric to decrease across K', 'value':'y'}], value=['y'], style={'display':'inline-block', 'marginRight':'12px'}),
            dcc.Checklist(id='prefer-xlsx', options=[{'label':'Use ImpactSearch .xlsx', 'value':'y'}], value=['y'], style={'display':'inline-block', 'marginRight':'12px'}),
            html.Label("ImpactSearch folder"),
            dcc.Input(id='xlsx-dir', type='text', value=DEFAULT_IMPACT_XLSX_DIR, style={'width':'40%'}),
        ], style={'marginBottom':'8px'}),
        html.Button("Run", id='run-btn'),
        # --- Lightweight progress UI ---
        html.Div(id='progress-wrap', style={'marginTop':'10px', 'display':'none'}, children=[
            html.Div(id='progress-text', style={'color':'#aaa','marginBottom':'4px'}),
            html.Div(style={'height':'10px','background':'#222','border':'1px solid #444','width':'60%'},
                     children=html.Div(id='progress-inner',
                                       style={'height':'100%','width':'0%','background':'#80ff00'}))
        ]),
        # --- Batch progress table ---
        html.Div(id='batch-progress-wrap', style={'marginTop':'10px', 'display':'block'}, children=[
            html.H5("Batch Progress"),
            html.Div(id='jobs-summary', style={'marginBottom':'6px', 'fontWeight':'bold', 'color':'#80ff00'}),
            dash_table.DataTable(
                id='jobs',
                columns=[
                    {'name':'Secondary','id':'Secondary'},
                    {'name':'Status','id':'Status'},
                    {'name':'Phase','id':'Phase'},
                    {'name':'%','id':'Percent'},
                    {'name':'Combos','id':'Combos'},
                    {'name':'ETA','id':'ETA'},
                    {'name':'Updated','id':'Updated'},
                    {'name':'Message','id':'Message'},
                    {'name':'Outdir','id':'Outdir'},
                ],
                data=[],
                page_size=20,
                style_table={'overflowX':'auto'}
            )
        ]),
        dcc.Interval(id='progress-interval', interval=1000, n_intervals=0, disabled=True),
        dcc.Interval(id='jobs-interval', interval=1000, n_intervals=0, disabled=True),
        dcc.Loading(
            id='loading',
            type='circle',
            children=[
                html.Div(id='run-status', style={'marginTop':'8px', 'whiteSpace':'pre-wrap'}),
                dash_table.DataTable(id='tbl', data=[], columns=[], page_size=10, style_table={'overflowX':'auto'})
            ]
        ),
        dcc.Store(id='last-outdir'),
        dcc.Store(id='progress-path'),
        dcc.Store(id='jobs-list')
    ])

    def _read_leaderboard(dirpath: str) -> pd.DataFrame:
        for ext in ('xlsx','parquet','csv'):
            p = os.path.join(dirpath, f"combo_leaderboard.{ext}")
            if os.path.exists(p):
                return pd.read_excel(p) if ext=='xlsx' else (pd.read_parquet(p) if ext=='parquet' else pd.read_csv(p))
        return pd.DataFrame()

    @app.callback(
        [Output('tbl','data'),
         Output('tbl','columns'),
         Output('run-status','children'),
         Output('last-outdir','data'),
         Output('progress-path','data'),
         Output('progress-interval','disabled'),
         Output('progress-wrap','style'),
         Output('jobs-list','data'),
         Output('jobs-interval','disabled'),
         Output('batch-progress-wrap','style')],
        [Input('run-btn','n_clicks')],
        [State('secondary-input','value'), State('primaries-input','value'),
         State('topn','value'), State('bottomn','value'), State('maxk','value'), State('exk','value'), State('alpha','value'),
         State('prefer-xlsx','value'), State('xlsx-dir','value'),
         State('min-trigger-days','value'), State('sharpe-eps','value'), State('seed-by','value'), State('optimize-by','value'), State('allow-decreasing','value')],
        prevent_initial_call=True
    )
    def _run(n, secondary, primaries_str, topn, bottomn, maxk, exk, alpha, prefer, xdir, min_trigger_days, sharpe_eps, seed_by, optimize_by, allow_decreasing):
        if not n:
            raise PreventUpdate
        if not secondary:
            return [], [], "Enter a Secondary.", None, None, True, {'display':'none'}, None, True, {'display':'none'}

        # Parse comma-separated secondaries
        secondaries = [s.strip().upper() for s in secondary.split(',') if s.strip()]
        if not secondaries:
            return [], [], "Enter at least one secondary ticker.", None, None, True, {'display':'none'}, None, True, {'display':'none'}

        # Validate ImpactSearch folder if fast-path is requested
        prefer_fast = ('y' in (prefer or []))
        if prefer_fast and (not xdir or not os.path.isdir(xdir)):
            return [], [], f"ImpactSearch folder not found: {xdir}", None, None, True, {'display':'none'}, None, True, {'display':'none'}

        primaries = []
        if primaries_str:
            primaries = [t.strip().upper() for t in re.split(r'[,\s]+', primaries_str) if t.strip()]

        # CRITICAL: Require primaries to be specified - no 72k fallback
        if not primaries and not prefer_fast:
            return [], [], "[ERROR] Primary tickers field is empty. Please enter one or more primary tickers.", None, None, True, {'display':'none'}, None, True, {'display':'none'}

        # Validate and sanitize the new parameters
        min_trigger_days_val = int(min_trigger_days) if min_trigger_days else 30
        sharpe_eps_val = float(sharpe_eps) if sharpe_eps else 0.01
        # Phase 6I-73: hard-pin to total_capture regardless of legacy
        # incoming values from older Dash session state.
        seed_by_val = 'total_capture'
        optimize_by_val = 'total_capture'
        allow_decreasing_val = ('y' in (allow_decreasing or []))
        exk_val = int(exk or 4)

        print(f"[STACKBUILDER] Run clicked -> secondaries={secondaries}  primaries={len(primaries)}  "
              f"topN={topn} bottomN={bottomn} maxK={maxk} exhaustiveK={exk_val} alpha={alpha} prefer_xlsx={prefer_fast} xlsx_dir={xdir} "
              f"min_trigger_days={min_trigger_days_val} sharpe_eps={sharpe_eps_val} seed_by={seed_by_val} optimize_by={optimize_by_val} allow_decreasing={allow_decreasing_val}")

        # Multi-secondary mode: create jobs list and spawn threads
        jobs = []
        for sec in secondaries:
            sec_clean = sec.replace('^','').replace('.','_')
            ppath = os.path.join(PROGRESS_ROOT, f"{sec_clean}_{int(time.time())}.json")
            try:
                _write_progress(ppath, status='running', phase='preflight', percent=1.0,
                                message=f"Starting {sec}...", secondary=sec, started_ts=time.time())
            except Exception:
                pass

            jobs.append({'secondary': sec, 'ppath': ppath})

            # Phase 1B-2B: honor the Dash-launched outdir (from
            # run_dash(outdir, port)) instead of hardcoding RUNS_ROOT.
            # Falls back to RUNS_ROOT only if no Dash outdir was set.
            _job_outdir = outdir if outdir else RUNS_ROOT
            args = SimpleNamespace(
                secondary=sec, secondaries=None, primaries=None,
                top_n=int(topn or 20), bottom_n=int(bottomn or 20), max_k=int(maxk or 6),
                alpha=float(alpha or 0.05), min_marginal_capture=0.0,
                threads='auto', outdir=_job_outdir,
                fail_on_missing_cache=False, serve=False, port=8054,
                prefer_impact_xlsx=prefer_fast, impact_xlsx_dir=xdir,
                impact_xlsx_max_age_days=45,
                min_trigger_days=min_trigger_days_val,
                sharpe_eps=sharpe_eps_val,
                seed_by=seed_by_val,
                optimize_by=optimize_by_val,
                search='beam', beam_width=12, exhaustive_k=exk_val,
                both_modes=False, k_patience=1,
                allow_decreasing=allow_decreasing_val,
                progress_path=ppath
            )

            # Phase 1B-2B: pass loop-iteration values into the worker
            # explicitly via Thread args. The previous closure-over-loop
            # body would have all started threads see the LAST iteration's
            # `args`, `sec`, `ppath`, and `primaries` once the for-loop
            # completed (Python late-binding closure semantics), causing
            # threads launched early in the loop to run with the wrong
            # secondary's parameters.
            primaries_snapshot = list(primaries) if primaries else None

            def _job(job_args, job_sec, job_ppath, job_primaries):
                try:
                    run_for_secondary(job_args, job_sec, specified_primaries=job_primaries)
                except BaseException as e:
                    import traceback
                    error_msg = str(e)
                    full_trace = traceback.format_exc()
                    print(f"[ERROR] Job failed for {job_sec}:\n{full_trace}")
                    _write_progress(job_ppath, status='failed', phase='error', percent=100.0,
                                    message=f"Error for {job_sec}: {e.__class__.__name__}: {error_msg}")

            threading.Thread(
                target=_job,
                args=(args, sec, ppath, primaries_snapshot),
                daemon=True,
            ).start()

        # Return immediately; batch polling callback will stream progress for all jobs
        summary = f"Started {len(secondaries)} job(s): {', '.join(secondaries)}"
        if len(secondaries) == 1:
            # Force the batch table even for a single job to surface ETA/outstanding counters
            return ([], [], summary, None, None,
                    True,  {'marginTop':'10px', 'display': 'none'},   # hide single progress bar
                    jobs, False, {'marginTop':'10px', 'display': 'block'})
        else:
            batch_mode = True
            return ([], [], summary, None, None,
                    False, {'marginTop':'10px', 'display': 'none'},
                    jobs, False, {'marginTop':'10px', 'display': 'block'})

    # Poll progress file and update status + table when ready
    @app.callback(
        [Output('run-status','children', allow_duplicate=True),
         Output('progress-inner','style'),
         Output('progress-text','children'),
         Output('tbl','data', allow_duplicate=True),
         Output('tbl','columns', allow_duplicate=True),
         Output('last-outdir','data', allow_duplicate=True),
         Output('progress-interval','disabled', allow_duplicate=True)],
        [Input('progress-interval','n_intervals')],
        [State('progress-path','data'), State('last-outdir','data')],
        prevent_initial_call=True
    )
    def _poll_progress(_ticks, ppath, last_dir):
        if not ppath or not os.path.exists(ppath):
            raise PreventUpdate
        try:
            with open(ppath, 'r', encoding='utf-8') as f:
                prog = json.load(f)
        except Exception:
            raise PreventUpdate

        pct = float(prog.get('percent') or 0.0)
        msg = str(prog.get('message') or '')
        stat = str(prog.get('status') or 'running')
        phase = str(prog.get('phase') or '')

        # Progress text
        progress_msg = f"Phase: {phase.upper()} - {msg}" if phase else msg

        # Try to surface incremental leaderboard if available
        outdir = prog.get('final_outdir') or prog.get('outdir') or last_dir
        data, cols = no_update, no_update
        if outdir and stat == 'complete':
            try:
                df = _read_leaderboard(outdir)
                if not df.empty:
                    data = _json_safe_records(df)  # JSON-safe
                    cols = [{'name': str(c), 'id': str(c)} for c in df.columns]
            except Exception:
                pass

        # Stop polling once complete/failed
        done = stat in ('complete', 'failed')
        status_msg = msg if done else f"Running: {msg}"

        return (status_msg,
                {'height':'100%','width':f'{pct:.0f}%','background':'#80ff00' if stat != 'failed' else '#ff0000'},
                progress_msg,
                data, cols, outdir, done)

    # Poll batch jobs and update batch progress table
    @app.callback(
        [Output('jobs','data'),
         Output('jobs-interval','disabled', allow_duplicate=True),
         Output('jobs-summary','children')],
        [Input('jobs-interval','n_intervals')],
        [State('jobs-list','data')],
        prevent_initial_call=True
    )
    def _poll_jobs(_ticks, jobs):
        if not jobs:
            raise PreventUpdate

        rows = []
        all_done = True
        agg_total = 0
        agg_done = 0
        now = time.time()

        for job in jobs:
            ppath = job['ppath']
            sec = job['secondary']
            if not os.path.exists(ppath):
                continue
            try:
                with open(ppath, 'r', encoding='utf-8') as f:
                    prog = json.load(f)
            except Exception:
                continue

            pct = float(prog.get('percent') or 0.0)
            msg = str(prog.get('message') or '')
            stat = str(prog.get('status') or 'running')
            phase = str(prog.get('phase') or '')
            outdir = prog.get('final_outdir') or prog.get('outdir') or ''
            counters = prog.get('counters') or {}
            started_ts = prog.get('started_ts') or 0

            # Stall detection: mark as stalled if no update for 10+ minutes
            try:
                mtime = os.path.getmtime(ppath)
                age = now - mtime
            except Exception:
                age = 0.0

            updated_str = f"{int(age)}s ago" if age < 3600 else f"{int(age//60)}m ago"
            if stat == 'running' and age >= 600:  # 10 minutes
                stat = 'stalled'

            # Calculate outstanding work and combos cell
            outstanding = ''
            combos_cell = ''
            if phase == 'ranking':
                done = counters.get('primaries_done', 0)
                total = counters.get('primaries_total', 0)
                if total > 0:
                    outstanding = f"{total - done} primaries"
            elif phase == 'stacking':
                done = counters.get('combos_tested', 0)
                total = counters.get('combos_total', 0)
                if total > 0:
                    rem = max(0, total - done)
                    outstanding = f"{rem:,} combos"
                    combos_cell = f"{done:,}/{total:,}"
                    agg_total += total
                    agg_done += min(done, total)

            # Calculate ETA
            eta = ''
            if stat == 'running' and phase == 'stacking' and counters.get('combos_total', 0) > 0:
                done = counters.get('combos_tested', 0)
                total = counters.get('combos_total', 0)
                if started_ts > 0 and done > 0:
                    elapsed = now - started_ts
                    rate = done / elapsed
                    if rate > 0:
                        rem = max(0, total - done)
                        remaining_sec = int(rem / rate)
                        if remaining_sec < 60:
                            eta = f"{remaining_sec}s"
                        elif remaining_sec < 3600:
                            eta = f"{remaining_sec // 60}m"
                        else:
                            eta = f"{remaining_sec // 3600}h {(remaining_sec % 3600) // 60}m"
            elif stat == 'running' and pct > 0 and started_ts > 0:
                elapsed = now - started_ts
                if pct >= 100:
                    eta = 'Done'
                else:
                    remaining_sec = elapsed * (100 - pct) / pct
                    if remaining_sec < 60:
                        eta = f"{int(remaining_sec)}s"
                    elif remaining_sec < 3600:
                        eta = f"{int(remaining_sec / 60)}m"
                    else:
                        eta = f"{int(remaining_sec / 3600)}h {int((remaining_sec % 3600) / 60)}m"

            rows.append({
                'Secondary': sec,
                'Status': stat,
                'Phase': phase.upper(),
                'Percent': f"{pct:.1f}%",
                'Combos': combos_cell,
                'ETA': eta,
                'Updated': updated_str,
                'Message': msg,
                'Outdir': outdir
            })

            if stat not in ('complete', 'failed'):
                all_done = False

        # Build summary line with global progress percentage
        active_count = sum(1 for r in rows if r['Status'] in ('running', 'stalled'))
        done_count = sum(1 for r in rows if r['Status'] == 'complete')
        total_jobs = len(rows)

        if agg_total > 0:
            global_pct = (agg_done / agg_total) * 100
            summary = f"Active: {active_count} | Done: {done_count}/{total_jobs} | Global progress: {agg_done:,}/{agg_total:,} ({global_pct:.1f}%)"
        else:
            summary = f"Active: {active_count} | Done: {done_count}/{total_jobs}"

        return rows, all_done, summary

    # Avoid double-execution noise and keep console logs visible
    app.run_server(debug=False, port=port, use_reloader=False)

# ---------- Orchestration ----------
def run_for_secondary(args, secondary: str, specified_primaries: Optional[List[str]] = None, *, grace_days: Optional[int] = None) -> str:
    # Ensure worker processes inherit the same runtime configuration as the parent.
    global OUTPUT_FORMAT, SIGNAL_LIB_DIR_RUNTIME, VERBOSE, COMBINE_INTERSECTION
    try:
        OUTPUT_FORMAT = getattr(args, 'output_format', OUTPUT_FORMAT)
        SIGNAL_LIB_DIR_RUNTIME = getattr(args, 'signal_lib_dir', SIGNAL_LIB_DIR_RUNTIME)
        VERBOSE = bool(getattr(args, 'verbose', VERBOSE))
        COMBINE_INTERSECTION = (getattr(args, 'combine_mode', 'intersection') == 'intersection')
    except Exception:
        pass

    start_time = time.time()
    # Phase 2B-2B: resolve grace once and thread it explicitly through
    # phase2_rank_all and phase3_build_stacks. Previously this site
    # mutated os.environ['IMPACT_CALENDAR_GRACE_DAYS'] when
    # args.grace_days was set, leaking grace state into worker
    # subprocesses and any subsequent in-process callers. The env
    # write is removed; an explicit kwarg-only ``grace_days`` parameter
    # on this function takes precedence over args.grace_days, and an
    # unset value falls back to DEFAULT_GRACE_DAYS via
    # _effective_grace_days.
    effective_grace = _effective_grace_days(
        grace_days if grace_days is not None
        else getattr(args, 'grace_days', None)
    )
    primaries_df, sec_rets, vendor_secondary = phase1_preflight(args, secondary, specified_primaries)
    ts = now_ts()
    # Clean secondary name for filesystem, but preserve '^' (safe on NTFS) per design
    vendor_secondary_clean = vendor_secondary.replace(".", "_")
    # Phase 1B-2B: honor args.outdir as the output root (CLI --outdir
    # was previously ignored by run_for_secondary; CLI single-secondary
    # and CLI multi-secondary paths therefore both wrote under
    # RUNS_ROOT regardless of --outdir).
    output_root = getattr(args, "outdir", None) or RUNS_ROOT
    secondary_parent = os.path.join(output_root, vendor_secondary_clean)
    ensure_dir(secondary_parent)
    # Use temporary directory initially within the parent
    # Make temp folder unique per process to avoid collisions under parallel runs
    temp_outdir = os.path.join(secondary_parent, f"temp_{ts}_{os.getpid()}")
    ensure_dir(temp_outdir)

    # Establish progress path from args or create new one
    ppath = getattr(args, 'progress_path', None)
    if not ppath:
        # Uniquify progress file as well
        ppath = os.path.join(PROGRESS_ROOT, f"{vendor_secondary_clean}_{os.getpid()}_{int(time.time())}.json")
    _write_progress(ppath, status='running', phase='preflight', percent=5.0,
                    message='Loading data and validating primaries...', outdir=temp_outdir,
                    secondary=vendor_secondary, started_ts=time.time())

    # Phase 3B-2A amendment: per-run input-manifest collector lives in a
    # ContextVar so two concurrent runs (Dash launches one threading.Thread
    # per secondary) get isolated collectors. The token returned by
    # _start_input_manifest_collection is captured here and passed into
    # _finalize_input_manifest_collection on success and in the except
    # path so the ContextVar is properly reset to its prior state.
    _collector_token = _start_input_manifest_collection()
    try:
        run_id = f"{vendor_secondary_clean}-{ts}-{os.getpid()}"
        manifest = {
            # ---- Phase 3A baseline keys (preserved for backwards compat) ----
            'secondary': vendor_secondary,
            'started_at': datetime.now().isoformat(),
            'params': {
                'alpha': args.alpha,
                'max_k': args.max_k,
                'top_n': args.top_n,
                'bottom_n': args.bottom_n,
                'min_trigger_days': getattr(args, 'min_trigger_days', 30),
                'sharpe_eps': getattr(args, 'sharpe_eps', 0.01),
                'seed_by': getattr(args, 'seed_by', 'total_capture')
            },
            # ---- Phase 3B-2A enrichment ---------------------------------
            'schema_version': _MANIFEST_SCHEMA_VERSION,
            'artifact_kind': _ARTIFACT_KIND_OUTPUT,
            'artifact_type': 'stackbuilder_run',
            'producer_engine': 'stackbuilder',
            'engine_version': '1.0.0',
            'run_id': run_id,
            'cli_args': _stable_cli_args_subset(args),
            'status': 'running',
        }
        # Capture runtime / git context once at start; the final write
        # picks up an updated build_timestamp from build_output_manifest.
        _ctx = _build_output_manifest(
            artifact_type='stackbuilder_run',
            producer_engine='stackbuilder',
            engine_version='1.0.0',
        )
        manifest['git_commit'] = _ctx['git_commit']
        manifest['git_dirty'] = _ctx['git_dirty']
        manifest['package_versions'] = _ctx['package_versions']
        manifest['build_timestamp'] = _ctx['build_timestamp']
        manifest['builder_identity'] = _ctx['builder_identity']
        manifest['host_platform'] = _ctx['host_platform']
        write_json(os.path.join(temp_outdir, 'run_manifest.json'), manifest)

        _write_progress(ppath, status='running', phase='ranking', percent=25.0,
                        message=f'Ranking {len(primaries_df)} primaries against {vendor_secondary}...',
                        outdir=temp_outdir, secondary=vendor_secondary)
        rank_all, rank_direct, rank_inverse = phase2_rank_all(args, primaries_df, sec_rets, temp_outdir, secondary=vendor_secondary, progress_path=ppath, grace_days=effective_grace)

        cohort_sz = args.top_n + args.bottom_n
        _write_progress(ppath, status='running', phase='stacking', percent=60.0,
                        message=f'Ranking complete. Building optimal stack from {cohort_sz} candidates...',
                        outdir=temp_outdir, secondary=vendor_secondary)

        # Progress wrapper: parse "K=" from msg and compute % without closing over final_members
        def _k_progress(msg: str = "", **kw):
            try:
                import re
                m = re.search(r'K=(\d+)', str(msg))
                k = int(m.group(1)) if m else 1
            except Exception:
                k = 1
            maxk = max(1, int(getattr(args, 'max_k', 6)))
            base, span = 60.0, 30.0
            pct = base + min(span, (k - 1) * (span / max(1, (maxk - 1))))
            _write_progress(ppath, status='running', phase='stacking', percent=pct,
                            message=msg, outdir=temp_outdir, secondary=vendor_secondary, **kw)

        # Pass progress callback to phase3
        leaderboard, final_members = phase3_build_stacks(
            args, rank_direct, rank_inverse, sec_rets, temp_outdir, progress_cb=_k_progress, grace_days=effective_grace
        )

        _write_progress(ppath, status='running', phase='finalizing', percent=90.0,
                        message='Finalizing results and generating output files...',
                        outdir=temp_outdir, secondary=vendor_secondary)

        # Phase 5C-2c amendment: durable validation MUST run before
        # the durable run is published (locked 5C-1 §3 fail-closed
        # contract). If even the failed-artifact write raises, we
        # propagate so the outer exception handler removes
        # temp_outdir; a complete StackBuilder run directory is
        # NEVER produced without locked validation summary keys.
        if (
            primaries_df is not None
            and not primaries_df.empty
            and "Primary Ticker" in primaries_df.columns
        ):
            validation_universe = primaries_df["Primary Ticker"].astype(str).tolist()
        elif rank_all is not None and not rank_all.empty and "Primary Ticker" in rank_all.columns:
            validation_universe = rank_all["Primary Ticker"].astype(str).tolist()
        else:
            validation_universe = []

        validation_run_id = generate_run_id("stackbuilder", "run_directory")
        # Intentionally NO try/except around this call. Normal
        # validation failures are absorbed inside the helper (it
        # writes a status='failed' artifact and returns a complete
        # manifest summary); only a fallback-write failure can
        # propagate, and that MUST abort the run so no manifest
        # without locked validation keys is ever published.
        _vcontract, validation_summary, _vsidecar = (
            _prepare_stackbuilder_durable_validation(
                args=args,
                secondary_ticker=vendor_secondary,
                primary_universe=validation_universe,
                run_id=validation_run_id,
                grace_days=effective_grace,
            )
        )
        _validate_stackbuilder_validation_summary(validation_summary)

        # Construct final directory name based on stack members
        if final_members:
            # Keep the mode indicators [D] or [I] in the name
            # Replace brackets with D or I suffix for filesystem compatibility
            member_names = []
            for m in final_members:
                if '[D]' in m:
                    member_names.append(m.replace('[D]', '-D'))
                elif '[I]' in m:
                    member_names.append(m.replace('[I]', '-I'))
                else:
                    member_names.append(m)
            # Include seed policy in folder name
            # Phase 6I-73 amendment: Total Capture is the only seed
            # policy; folder names never advertise a Sharpe seed.
            seed_tag = "seedTC"
            final_name = f"{vendor_secondary_clean}__{seed_tag}__{('_'.join(member_names))}"
            # Truncate if too long for filesystem (Windows limit is 260 chars for full path)
            if len(final_name) > 200:
                final_name = final_name[:197] + "..."
        else:
            # No stack was built - use special name
            # Phase 6I-73 amendment: Total Capture is the only seed
            # policy; folder names never advertise a Sharpe seed.
            seed_tag = "seedTC"
            final_name = f"{vendor_secondary_clean}__{seed_tag}__no_stack"

        # Place in the secondary's parent directory
        final_outdir = os.path.join(secondary_parent, final_name.replace(f"{vendor_secondary_clean}__", ""))

        # Handle existing directory by replacing it (or adding timestamp if you prefer to keep history)
        if os.path.exists(final_outdir):
            shutil.rmtree(final_outdir, ignore_errors=True)

        # Rename the temp directory to final name
        shutil.move(temp_outdir, final_outdir)

        _write_progress(ppath, status='complete', phase='done', percent=100.0,
                        message=f'Complete! Results saved to: {final_outdir}',
                        final_outdir=final_outdir, secondary=vendor_secondary)

        # Generate summary statistics (ensure all values are JSON-safe)
        elapsed_time = time.time() - start_time

        # Convert numpy types to Python native types for JSON serialization
        def to_native(val):
            import numpy as np
            if isinstance(val, (np.integer, np.int64)):
                return int(val)
            elif isinstance(val, (np.floating, np.float64)):
                return float(val)
            return val

        summary = {
            'secondary': vendor_secondary,
            'run_timestamp': ts,
            'elapsed_seconds': round(elapsed_time, 2),
            'elapsed_formatted': str(timedelta(seconds=int(elapsed_time))),
            'primaries_tested': int(len(primaries_df)) if primaries_df is not None else 0,
            'final_stack_size': len(final_members),
            'best_sharpe': to_native(leaderboard.iloc[-1]['Sharpe Ratio']) if not leaderboard.empty else None,
            'best_capture': to_native(leaderboard.iloc[-1]['Total Capture (%)']) if not leaderboard.empty else None,
            'best_trigger_days': to_native(leaderboard.iloc[-1]['Trigger Days']) if not leaderboard.empty else None,
            'parameters': {
                'top_n': int(args.top_n),
                'bottom_n': int(args.bottom_n),
                'max_k': int(args.max_k),
                'min_trigger_days': int(getattr(args, 'min_trigger_days', 30)),
                'sharpe_eps': float(getattr(args, 'sharpe_eps', 1e-6)),
                'seed_by': str(getattr(args, 'seed_by', 'sharpe')),
                'search': str(getattr(args, 'search', 'beam')),
                'combine_mode': str(getattr(args, 'combine_mode', 'intersection'))
            }
        }
        write_json(os.path.join(final_outdir, 'summary.json'), summary)

        manifest['finished_at'] = datetime.now().isoformat()
        manifest['elapsed_seconds'] = round(elapsed_time, 2)
        ext = 'xlsx' if OUTPUT_FORMAT=='xlsx' else ('parquet' if OUTPUT_FORMAT=='parquet' else 'csv')
        # Preserve the existing flat outputs mapping for backwards compat.
        # Phase 6I-73: rank_inverse is no longer persisted; not listed.
        manifest['outputs'] = {
            'rank_all': f'rank_all.{ext}',
            'rank_direct': f'rank_direct.{ext}',
            'cohort': f'cohort.{ext}',
            'leaderboard': f'combo_leaderboard.{ext}'
        }
        # Phase 3B-2A: detailed output artifact entries with file SHAs
        # alongside (not replacing) the legacy 'outputs' mapping.
        manifest['output_artifacts'] = _build_output_artifacts(final_outdir)
        # Phase 3B-2A: which signal libraries this run consumed. The
        # collector was active across all load_lib_or_none calls.
        input_summary = _finalize_input_manifest_collection(_collector_token)
        _collector_token = None
        manifest.update(input_summary)
        manifest['input_secondary_hash'] = None  # populated by 3B-2B once
                                                 # secondary fingerprinting lands

        # Phase 5C-2c: inject the locked 10 validation summary keys
        # into the manifest. ``validation_summary`` was produced
        # BEFORE the run was published (see the validation block
        # above phase3->finalize), and was already validated against
        # _LOCKED_VALIDATION_SUMMARY_KEYS. Re-validate here as a
        # belt-and-suspenders gate so a complete manifest can NEVER
        # be written without the locked keys.
        _validate_stackbuilder_validation_summary(validation_summary)
        for _vk in _LOCKED_VALIDATION_SUMMARY_KEYS:
            manifest[_vk] = validation_summary[_vk]

        manifest['status'] = 'complete'
        write_json(os.path.join(final_outdir, 'run_manifest.json'), manifest)
        print(f"[COMPLETE] Secondary {vendor_secondary} finished in {elapsed_time:.1f}s")
        print(f"[RESULT] Best stack K={len(final_members)}: Sharpe={summary['best_sharpe']:.3f}, Capture={summary['best_capture']:.2f}%, TD={summary['best_trigger_days']}")
        print(f"[OUTPUT] Results saved to: {final_outdir}")
        for _vline in _stackbuilder_validation_completion_lines(validation_summary):
            print(f"[VALIDATION] {_vline}")
        return final_outdir

    except Exception as e:
        # Clean up temp directory on failure
        if os.path.exists(temp_outdir):
            shutil.rmtree(temp_outdir, ignore_errors=True)
            print(f"[CLEANUP] Removed temp directory: {temp_outdir}")
        # Phase 3B-2A: drop the per-run input-manifest collector so a
        # failed run does not bleed into the next run's manifest.
        # Phase 3B-2A amendment: pass the token so the ContextVar is
        # restored to its prior state, even on exception paths.
        if _collector_token is not None:
            _finalize_input_manifest_collection(_collector_token)
        raise

# ---------------------------------------------------------------------------
# Phase 5C-2c: validation integration
# ---------------------------------------------------------------------------


_LOCKED_VALIDATION_SUMMARY_KEYS = (
    "validation_contract_version",
    "validation_status",
    "n_strategies_tested",
    "n_strategies_reported",
    "multiple_comparisons_control_method",
    "multiple_comparisons_control_alpha",
    "walk_forward_n_folds",
    "mean_baseline_sharpe",
    "validation_artifact_path",
    "validation_artifact_hash",
)


def _validate_stackbuilder_validation_summary(
    validation_summary: Mapping[str, Any],
) -> None:
    """Raise ValueError naming the missing key when the locked 5C
    validation manifest summary is incomplete. Used at the
    run_manifest write gate so durable runs fail before producing a
    manifest without locked validation fields.
    """
    for key in _LOCKED_VALIDATION_SUMMARY_KEYS:
        if key not in validation_summary:
            raise ValueError(
                f"validation_summary missing required key: {key!r}"
            )


def _stackbuilder_load_secondary_with_cutoff(
    secondary_ticker: str,
    cutoff: Optional[pd.Timestamp] = None,
):
    """Load the secondary price frame and optionally slice to cutoff.

    Default ``cutoff=None`` preserves existing loader behavior.
    """
    df = load_secondary_prices(secondary_ticker)
    if df is None or df.empty:
        return df
    if cutoff is None:
        return df
    return slice_to_cutoff(df, cutoff)


def _stackbuilder_safe_float(x):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f


def _stackbuilder_canonical_strategy_id(
    members: Sequence[Tuple[str, str]],
    secondary_ticker: str,
) -> str:
    """Stable canonical strategy id for a stack: deterministic
    member-mode ordering, uppercase, then ``__{SECONDARY}``.
    """
    canon = sorted((str(t).upper(), str(m).upper()) for (t, m) in members)
    label = ",".join(f"{t}[{m}]" for (t, m) in canon)
    sec = str(secondary_ticker or "").strip().upper()
    return f"STACKBUILDER({label})__{sec}"


def _build_failed_validation_contract(
    *,
    run_id: str,
    producer_engine: str,
    app_surface: str,
    secondary_ticker: str,
    primary_universe: Sequence[str],
    failure_reason: str,
    exception_repr: str,
) -> dict:
    """Construct a minimal ``validation_contract_v1`` artifact with
    ``validation_status='failed'``. Used when durable StackBuilder
    validation raises before a normal contract can be completed; the
    failed contract MUST still satisfy the locked
    ``write_validation_sidecar`` schema check.
    """
    formatted = (
        f"[STACKBUILDER:validation_failed] run {run_id}: "
        f"{failure_reason} ({exception_repr})"
    )
    return {
        "validation_contract_version": VALIDATION_CONTRACT_VERSION,
        "validation_methodology_version": VALIDATION_METHODOLOGY_VERSION,
        "validation_status": "failed",
        "run_id": run_id,
        "producer_engine": producer_engine,
        "app_surface": app_surface,
        "evaluation_time": datetime.now(timezone.utc).isoformat(),
        "data_available_through": None,
        "in_sample_window_start": None,
        "in_sample_window_end": None,
        "oos_window_start": None,
        "oos_window_end": None,
        "walk_forward_n_folds": None,
        "outcome_windows": list(DEFAULT_OUTCOME_WINDOWS),
        "baseline_method": "same_ticker_buy_and_hold",
        "n_strategies_tested": 0,
        "n_strategies_reported": 0,
        "n_strategies_survived_empirical": 0,
        "multiple_comparisons_control_method": "benjamini_hochberg",
        "multiple_comparisons_control_alpha": float(DEFAULT_ALPHA),
        "multiple_comparisons_supplementary": "bonferroni",
        "n_permutations": 0,
        "n_bootstrap_samples": 0,
        "borderline_tolerance_multiplier": float(
            DEFAULT_BORDERLINE_TOLERANCE_MULTIPLIER,
        ),
        "baseline_per_fold": [],
        "baseline_aggregate": {
            "n_folds_with_baseline": 0,
            "mean_baseline_sharpe": None,
            "mean_baseline_return": None,
            "total_baseline_observations": 0,
        },
        "survivorship_summary": {
            "total_tested": 0,
            "total_reported_bh": 0,
            "total_empirical_validated": 0,
            "total_empirical_not_run": 0,
            "did_not_survive_bh": 0,
            "did_not_survive_empirical": 0,
            "did_not_survive_no_triggers": 0,
            "did_not_survive_insufficient_history": 0,
        },
        "issues": [formatted],
        "strategies": [],
    }


def _empty_stackbuilder_strategy_fold_result(
    fold_index: int,
    candidate: StrategyCandidate,
    reason: str,
) -> StrategyFoldResult:
    """Return an empty StrategyFoldResult tagged with a formatted
    [STACKBUILDER:validation_unavailable] issue.
    """
    formatted = (
        f"[STACKBUILDER:validation_unavailable] {candidate.strategy_id}: "
        f"{reason}. Action: inspect adapter logs or extend history."
    )
    return StrategyFoldResult(
        fold_index=fold_index,
        strategy_id=candidate.strategy_id,
        strategy_label=candidate.strategy_label,
        daily_capture=pd.Series([], dtype=FLOAT_DTYPE),
        trigger_mask=pd.Series([], dtype=bool),
        issues=(formatted,),
        metadata={"reason": reason},
    )


class StackBuilderValidationAdapter:
    """Phase 5C-2c SelectionAdapter for StackBuilder per-app validation.

    Walks the same phase2 / phase3 search space the production
    pipeline runs, but with cutoff-filtered signal libraries and
    secondary returns so each fold's selection only sees data
    ``<= context.selection_cutoff``. Evaluation reuses
    ``_signals_aligned_and_mask`` + ``_combine_signals`` +
    ``_captures_from_signals`` over the test window so candidate
    semantics match production. Baseline is same-ticker buy-and-hold
    on the secondary over the test window per locked 5C-1 §6.
    """

    def __init__(
        self,
        *,
        args,
        secondary_ticker: str,
        primary_universe: Sequence[str],
        scratch_dir,
        grace_days: Optional[int] = None,
    ) -> None:
        self.args = args
        self.secondary_ticker = str(secondary_ticker or "").strip().upper()
        self.primary_universe = [
            str(t or "").strip().upper()
            for t in (primary_universe or [])
            if str(t or "").strip()
        ]
        self.scratch_dir = Path(scratch_dir)
        self.grace_days = grace_days
        self._sec_df_cache = None

    def _secondary_frame(self):
        if self._sec_df_cache is None:
            self._sec_df_cache = _stackbuilder_load_secondary_with_cutoff(
                self.secondary_ticker, cutoff=None,
            )
        return self._sec_df_cache

    def history_index(self) -> pd.DatetimeIndex:
        df = self._secondary_frame()
        if df is None or df.empty:
            return pd.DatetimeIndex([])
        return df.index

    def select_for_fold(self, context: FoldContext):
        sec_df = self._secondary_frame()
        if sec_df is None or sec_df.empty:
            return []
        train_sec_df = slice_to_cutoff(sec_df, context.selection_cutoff)
        if train_sec_df is None or train_sec_df.empty:
            return []
        train_sec_rets = pct_returns(train_sec_df["Close"])
        primaries_df = pd.DataFrame(
            {"Primary Ticker": list(self.primary_universe)}
        )

        scratch = self.scratch_dir / f"fold_{context.fold_index}"
        scratch.mkdir(parents=True, exist_ok=True)

        # Force XLSX fastpath OFF for validation: locked 5C contract
        # forbids using full-history XLSX metrics inside fold
        # selection. ``data_available_through`` is the secondary
        # defense (skips the fastpath even if some caller leaves
        # prefer_impact_xlsx=True).
        args2 = copy.copy(self.args)
        try:
            args2.prefer_impact_xlsx = False
            args2.no_progress = True
        except Exception:
            pass

        try:
            rank_all, rank_direct, rank_inverse = phase2_rank_all(
                args2, primaries_df, train_sec_rets, str(scratch),
                secondary=self.secondary_ticker,
                progress_path=None,
                grace_days=self.grace_days,
                data_available_through=context.selection_cutoff,
            )
        except SystemExit:
            return []
        except Exception:
            return []

        collected: List[dict] = []

        def _phase3_collector(record: dict) -> None:
            collected.append(record)

        args3 = copy.copy(self.args)
        try:
            args3.no_progress = True
        except Exception:
            pass

        try:
            phase3_build_stacks(
                args3, rank_direct, rank_inverse, train_sec_rets,
                str(scratch),
                progress_cb=None,
                grace_days=self.grace_days,
                data_available_through=context.selection_cutoff,
                validation_collector=_phase3_collector,
            )
        except SystemExit:
            # phase3 raises SystemExit when no single passed the
            # min Trigger Days gate; that's not a validation
            # failure, just an empty fold.
            pass
        except Exception:
            pass

        candidates: List[StrategyCandidate] = []
        seen_canon: set = set()
        for rec in collected:
            members_tuple = rec.get("members") or ()
            try:
                canon = tuple(sorted(
                    (str(t).upper(), str(m).upper())
                    for (t, m) in members_tuple
                ))
            except Exception:
                continue
            if not canon:
                continue
            if canon in seen_canon:
                continue
            seen_canon.add(canon)
            sid = _stackbuilder_canonical_strategy_id(
                canon, self.secondary_ticker,
            )
            candidates.append(StrategyCandidate(
                strategy_id=sid,
                strategy_label=sid,
                app_payload={
                    "members": list(canon),
                    "secondary_ticker": self.secondary_ticker,
                    "k": int(rec.get("k") or len(canon)),
                    "search_source": str(rec.get("search_source") or ""),
                    "in_sample_metrics": dict(
                        rec.get("in_sample_metrics") or {}
                    ),
                    "in_sample_rejected_reason": rec.get(
                        "in_sample_rejected_reason"
                    ),
                },
            ))
        return candidates

    def evaluate_candidate(
        self,
        candidate: StrategyCandidate,
        context: FoldContext,
    ) -> StrategyFoldResult:
        sec_df = self._secondary_frame()
        if sec_df is None or sec_df.empty:
            return _empty_stackbuilder_strategy_fold_result(
                context.fold_index, candidate,
                reason="secondary frame unavailable for fold",
            )
        eval_sec_df = slice_to_cutoff(sec_df, context.evaluation_cutoff)
        if eval_sec_df is None or eval_sec_df.empty:
            return _empty_stackbuilder_strategy_fold_result(
                context.fold_index, candidate,
                reason="secondary frame empty under evaluation cutoff",
            )
        test_window = slice_between(
            eval_sec_df, context.test_start, context.test_end,
        )
        if test_window is None or test_window.empty:
            return _empty_stackbuilder_strategy_fold_result(
                context.fold_index, candidate,
                reason="empty secondary frame over test window",
            )
        test_index = test_window.index
        test_sec_rets = pct_returns(test_window["Close"])

        members = list((candidate.app_payload or {}).get("members") or [])
        if not members:
            return _empty_stackbuilder_strategy_fold_result(
                context.fold_index, candidate,
                reason="candidate has no members",
            )

        member_signals: List[Tuple[pd.Series, pd.Series]] = []
        for entry in members:
            try:
                t, m = entry[0], entry[1]
            except Exception:
                continue
            try:
                sig, present = _signals_aligned_and_mask(
                    str(t), str(m), test_index,
                    grace_days=self.grace_days,
                    data_available_through=context.evaluation_cutoff,
                )
            except Exception as exc:
                return _empty_stackbuilder_strategy_fold_result(
                    context.fold_index, candidate,
                    reason=(
                        f"_signals_aligned_and_mask raised "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
            member_signals.append((sig, present))

        if not member_signals:
            return _empty_stackbuilder_strategy_fold_result(
                context.fold_index, candidate,
                reason="no member produced an aligned signal series",
            )

        signal_list = [s for (s, _p) in member_signals]
        comb_sig = _combine_signals(signal_list)
        present_all = member_signals[0][1].copy()
        for (_s, p) in member_signals[1:]:
            present_all &= p
        comb_sig = comb_sig.where(present_all, 'None')
        daily_capture = _captures_from_signals(comb_sig, test_sec_rets)
        trigger_mask = comb_sig.isin(['Buy', 'Short']).astype(bool)

        if daily_capture.empty or not bool(trigger_mask.any()):
            return _empty_stackbuilder_strategy_fold_result(
                context.fold_index, candidate,
                reason="no triggers in evaluation window",
            )

        return StrategyFoldResult(
            fold_index=context.fold_index,
            strategy_id=candidate.strategy_id,
            strategy_label=candidate.strategy_label,
            daily_capture=daily_capture.astype(FLOAT_DTYPE),
            trigger_mask=trigger_mask,
            metadata={
                "signal_state": comb_sig,
                "permutation_return_pool": test_sec_rets,
                "members": list(members),
                "secondary_ticker": self.secondary_ticker,
            },
            issues=(),
        )

    def baseline_for_fold(self, context: FoldContext) -> BaselineFoldMetrics:
        sec_df = self._secondary_frame()
        if sec_df is None or sec_df.empty:
            formatted = (
                f"[STACKBUILDER:validation_baseline_unavailable] "
                f"fold-{context.fold_index}: secondary frame unavailable. "
                f"Action: confirm secondary ticker is valid."
            )
            return BaselineFoldMetrics(
                fold_index=context.fold_index, n_observations=0,
                baseline_sharpe=None, baseline_total_return=None,
                baseline_mean_return=None, baseline_std=None,
                issues=(formatted,),
            )
        test_window = slice_between(
            sec_df, context.test_start, context.test_end,
        )
        if test_window is None or test_window.empty:
            formatted = (
                f"[STACKBUILDER:validation_baseline_unavailable] "
                f"fold-{context.fold_index}: empty secondary frame over "
                f"baseline test window. "
                f"Action: extend the secondary history or relax fold cutoffs."
            )
            return BaselineFoldMetrics(
                fold_index=context.fold_index, n_observations=0,
                baseline_sharpe=None, baseline_total_return=None,
                baseline_mean_return=None, baseline_std=None,
                issues=(formatted,),
            )
        prices = test_window["Close"].astype(float)
        daily_returns = prices.pct_change().fillna(0.0) * 100.0
        n_obs = int(len(daily_returns))
        if n_obs == 0:
            return BaselineFoldMetrics(
                fold_index=context.fold_index, n_observations=0,
                baseline_sharpe=None, baseline_total_return=None,
                baseline_mean_return=None, baseline_std=None,
            )
        all_true = pd.Series([True] * n_obs, index=daily_returns.index)
        try:
            score = _canonical_score_captures(
                daily_returns, all_true,
                risk_free_rate=RISK_FREE_ANNUAL,
                periods_per_year=252,
                ddof=1,
            )
        except Exception as exc:
            formatted = (
                f"[STACKBUILDER:validation_baseline_unavailable] "
                f"fold-{context.fold_index}: baseline scoring raised "
                f"{type(exc).__name__}: {exc}. "
                f"Action: inspect canonical scoring."
            )
            return BaselineFoldMetrics(
                fold_index=context.fold_index, n_observations=n_obs,
                baseline_sharpe=None, baseline_total_return=None,
                baseline_mean_return=None, baseline_std=None,
                issues=(formatted,),
            )
        return BaselineFoldMetrics(
            fold_index=context.fold_index,
            n_observations=n_obs,
            baseline_sharpe=_stackbuilder_safe_float(
                getattr(score, "sharpe", None),
            ),
            baseline_total_return=_stackbuilder_safe_float(
                getattr(score, "total_capture", None),
            ),
            baseline_mean_return=_stackbuilder_safe_float(
                getattr(score, "avg_daily_capture", None),
            ),
            baseline_std=_stackbuilder_safe_float(
                getattr(score, "std_dev", None),
            ),
        )


def _prepare_stackbuilder_durable_validation(
    *,
    args,
    secondary_ticker: str,
    primary_universe: Sequence[str],
    run_id: str,
    grace_days: Optional[int] = None,
):
    """Phase 5C-2c fail-closed durable validation prepare.

    Returns ``(contract, validation_summary, sidecar_path)``.

    Normal path: drive ``validate_strategy_set`` with the
    StackBuilder adapter, persist ``validation.json`` (success path
    write uses ``allow_overwrite=False`` per locked 5C-1 §12), and
    re-validate the manifest summary against
    ``_LOCKED_VALIDATION_SUMMARY_KEYS``.

    Failure path: build a status='failed' contract via
    ``_build_failed_validation_contract``, persist via
    ``write_validation_sidecar(... allow_overwrite=True)`` so a
    post-sidecar failure (matching the 5C-2b second-amendment
    pattern) replaces the partial sidecar with the canonical failed
    contract. The ``allow_overwrite=True`` is bounded to this run's
    own run_id directory.

    If even the failed-artifact write raises, propagate so the
    caller can decide whether to abort the run_manifest write.
    """
    n_perm = int(getattr(args, "validation_n_permutations", 10000) or 10000)
    n_boot = int(
        getattr(args, "validation_n_bootstrap_samples", 10000) or 10000
    )
    rng_seed = getattr(args, "validation_rng_seed", None)
    init_train = int(
        getattr(args, "validation_initial_train_days", DEFAULT_INITIAL_TRAIN_DAYS)
        or DEFAULT_INITIAL_TRAIN_DAYS
    )
    test_window = int(
        getattr(args, "validation_test_window_days", DEFAULT_TEST_WINDOW_DAYS)
        or DEFAULT_TEST_WINDOW_DAYS
    )
    step = int(
        getattr(args, "validation_step_days", DEFAULT_STEP_DAYS)
        or DEFAULT_STEP_DAYS
    )

    output_dir = Path(VALIDATION_OUTPUT_BASE_DIR) / run_id

    try:
        with tempfile.TemporaryDirectory(
            prefix=f"sb_validation_{run_id}_",
        ) as scratch_str:
            adapter = StackBuilderValidationAdapter(
                args=args,
                secondary_ticker=secondary_ticker,
                primary_universe=primary_universe,
                scratch_dir=scratch_str,
                grace_days=grace_days,
            )
            history_index = adapter.history_index()
            contract = validate_strategy_set(
                adapter, history_index,
                run_id=run_id,
                producer_engine="stackbuilder",
                app_surface="run_directory",
                alpha=DEFAULT_ALPHA,
                initial_train_days=init_train,
                test_window_days=test_window,
                step_days=step,
                n_permutations=n_perm,
                n_bootstrap_samples=n_boot,
                borderline_tolerance_multiplier=(
                    DEFAULT_BORDERLINE_TOLERANCE_MULTIPLIER
                ),
                rng_seed=rng_seed,
            )
        sidecar_path = write_validation_sidecar(
            contract, output_dir, allow_overwrite=False,
        )
        artifact_hash = compute_validation_artifact_hash(sidecar_path)
        validation_summary = extract_manifest_summary(
            contract,
            validation_artifact_path=str(sidecar_path),
            validation_artifact_hash=artifact_hash,
        )
        _validate_stackbuilder_validation_summary(validation_summary)
        return contract, validation_summary, sidecar_path
    except Exception as primary_exc:
        failure_reason = (
            "StackBuilder durable validation failed during normal run"
        )
        exception_repr = (
            f"{type(primary_exc).__name__}: {primary_exc}"
        )
        _validation_logger.warning(
            "[5C-2c] durable validation falling back to failed artifact "
            "for %s: %s", secondary_ticker, exception_repr,
        )
        failed_contract = _build_failed_validation_contract(
            run_id=run_id,
            producer_engine="stackbuilder",
            app_surface="run_directory",
            secondary_ticker=secondary_ticker,
            primary_universe=list(primary_universe or []),
            failure_reason=failure_reason,
            exception_repr=exception_repr,
        )
        failed_sidecar_path = write_validation_sidecar(
            failed_contract, output_dir, allow_overwrite=True,
        )
        artifact_hash = compute_validation_artifact_hash(failed_sidecar_path)
        failed_summary = extract_manifest_summary(
            failed_contract,
            validation_artifact_path=str(failed_sidecar_path),
            validation_artifact_hash=artifact_hash,
        )
        _validate_stackbuilder_validation_summary(failed_summary)
        return failed_contract, failed_summary, failed_sidecar_path


def _stackbuilder_validation_completion_lines(
    validation_summary: Optional[Mapping[str, Any]],
) -> List[str]:
    """Phase 5C-2c: operator-visible validation completion lines.

    Returns ``[]`` when no validation summary is present so existing
    completion prints stay unchanged for non-validation paths.
    """
    out: List[str] = []
    if not isinstance(validation_summary, Mapping):
        return out
    status = validation_summary.get("validation_status")
    artifact_path = (
        validation_summary.get("validation_artifact_path") or "n/a"
    )
    if status == "failed":
        issues = validation_summary.get("issues") or []
        if not issues:
            # Fall back to a generic message; the contract issues
            # may live only in the sidecar JSON, not in the summary.
            first_issue = "see sidecar for details"
        else:
            first_issue = str(issues[0])
        out.append(
            f"Validation: FAILED - {first_issue}. "
            f"Sidecar: {artifact_path}"
        )
        return out
    n_tested = validation_summary.get("n_strategies_tested")
    n_reported = validation_summary.get("n_strategies_reported")
    alpha = validation_summary.get("multiple_comparisons_control_alpha")
    mean_baseline = validation_summary.get("mean_baseline_sharpe")
    if mean_baseline is None:
        baseline_text = "n/a"
    else:
        try:
            mb = float(mean_baseline)
            if not np.isfinite(mb):
                baseline_text = "n/a"
            else:
                baseline_text = f"{mb:.2f}"
        except (TypeError, ValueError):
            baseline_text = "n/a"
    out.append(
        f"Validation: {n_reported} of {n_tested} survived BH at "
        f"alpha={alpha}. Mean baseline Sharpe: {baseline_text}. "
        f"Sidecar: {artifact_path}"
    )
    return out


# Phase 5B Item 1: vestigial CLI deprecations. Each entry is
# (flag_name, message). Flags remain parseable with their existing
# defaults; the warning fires only when the flag is explicitly
# supplied on the command line. Module-level so future audits can
# grep for the deprecation set in one place.
_DEPRECATED_CLI_FLAGS = (
    (
        "--alpha",
        "[STACKBUILDER:DEPRECATED] --alpha: accepted for one release "
        "cycle but no longer changes StackBuilder scoring or selection; "
        "it is recorded as legacy metadata only and will be removed in "
        "a future cleanup.",
    ),
    (
        "--min-marginal-capture",
        "[STACKBUILDER:DEPRECATED] --min-marginal-capture: has no "
        "effect in the current StackBuilder search path; no replacement. "
        "Will be removed in a future cleanup.",
    ),
    (
        "--fail-on-missing-cache",
        "[STACKBUILDER:DEPRECATED] --fail-on-missing-cache: has no "
        "effect; use --prefer-impact-xlsx / --strict-manifests for "
        "current fast-path behavior where applicable. Will be removed "
        "in a future cleanup.",
    ),
)


def _emit_deprecated_cli_warnings(argv):
    """Emit a stderr warning for each explicitly-supplied deprecated
    CLI flag. Detection scans raw argv (parsed defaults can't tell
    "user passed default" from "user supplied flag explicitly"); the
    ``--flag=value`` form is supported alongside ``--flag value``.

    ``argv=None`` is the real command-line invocation path (argparse
    falls back to ``sys.argv[1:]``); this helper mirrors that fallback
    so warnings work both in tests and at the actual CLI. Each
    deprecated flag emits its warning at most once per parse.
    """
    raw = sys.argv[1:] if argv is None else list(argv)
    for flag, message in _DEPRECATED_CLI_FLAGS:
        prefix = flag + "="
        for tok in raw:
            if tok == flag or tok.startswith(prefix):
                print(message, file=sys.stderr)
                break


def parse_args(argv=None):
    _emit_deprecated_cli_warnings(argv)
    p = argparse.ArgumentParser(description="PRJCT9 StackBuilder (Signal Library only)")
    p.add_argument('--secondary', help='Secondary ticker')
    p.add_argument('--secondaries', help='Comma-separated list of secondaries', default=None)
    p.add_argument('--signal-lib-dir', default=DEFAULT_SIGNAL_LIB_DIR, help='Path to signal_library/data/stable')
    # Curated primaries
    p.add_argument('--primaries', help='Comma-separated list of primary tickers to analyze (if not set, uses master list or discovers from signal library)', default=None)
    p.add_argument('--top-n', type=int, default=20)
    p.add_argument('--bottom-n', type=int, default=20)
    p.add_argument('--max-k', type=int, default=6)
    p.add_argument('--alpha', type=float, default=0.05)
    p.add_argument('--min-marginal-capture', type=float, default=0.0)
    # Search / selection controls
    p.add_argument('--min-trigger-days', type=int, default=30,
                   help='Minimum Trigger Days required for any accepted stack')
    p.add_argument('--sharpe-eps', type=float, default=1e-6,
                   help='Strict Sharpe improvement per K must exceed this epsilon')
    # Phase 6I-73: Sharpe is no longer a supported selection criterion.
    # Total Capture is the only supported seed / optimize metric.
    p.add_argument('--seed-by', choices=['total_capture'], default='total_capture',
                   help='Metric to choose the initial K=1 seed (Total Capture only)')
    p.add_argument('--optimize-by', choices=['total_capture'], default=None,
                   help='Metric to optimize for K>=2 (Total Capture only; defaults to seed-by)')
    p.add_argument('--allow-decreasing', action='store_true',
                   help='Allow metric to decrease across K levels (find best at each K independently)')
    p.add_argument('--grace-days', type=int, default=None,
                   help=('Max calendar pad when aligning primary signals to '
                         'secondary. Unset (None) uses DEFAULT_GRACE_DAYS=10 '
                         'per spec §20; explicit 0 means strict mode '
                         '(spymaster-parity).'))
    p.add_argument('--search', choices=['greedy','beam','exhaustive'], default='beam',
                   help='Combinatorics strategy for K>1')
    p.add_argument('--beam-width', type=int, default=12,
                   help='Beam width for beam search')
    p.add_argument('--exhaustive-k', type=int, default=4,
                   help='Enumerate all combinations up to this K')
    p.add_argument('--both-modes', action='store_true',
                   help='Evaluate both Direct and Inverse modes for each candidate ticker')
    p.add_argument('--k-patience', type=int, default=0,
                   help='Number of K levels to continue searching even without Sharpe improvement')
    p.add_argument('--combine-mode', choices=['intersection','union'], default='intersection',
                   help='How to combine trigger days: intersection (all members) or union (any member)')
    p.add_argument('--verbose', action='store_true', help='Enable verbose diagnostic output')
    p.add_argument('--no-progress', action='store_true', help='Disable progress bars')
    p.add_argument('--save-stats', action='store_true', help='Save search statistics to JSON')
    p.add_argument('--threads', default=os.environ.get('STACKBUILDER_THREADS', 'auto'))
    p.add_argument('--outdir', default=RUNS_ROOT)
    p.add_argument('--fail-on-missing-cache', action='store_true')
    p.add_argument('--serve', action='store_true', help='Start minimal Dash to display results')
    p.add_argument('--port', type=int, default=8054)
    # ImpactSearch Excel + output format
    p.add_argument('--prefer-impact-xlsx', action='store_true', help='Prefer ImpactSearch .xlsx ranking if available')
    p.add_argument('--impact-xlsx-dir', default=DEFAULT_IMPACT_XLSX_DIR, help='Folder with ImpactSearch .xlsx exports')
    p.add_argument('--impact-xlsx-max-age-days', type=int, default=45)
    # Phase 3B-2B: strict manifest verification for the ImpactSearch
    # XLSX fast-path. Default off; when enabled, legacy/missing/mismatched
    # workbooks are rejected and the run falls back to the slow path or
    # exits with a fatal error if no primaries were provided.
    p.add_argument(
        '--strict-manifests', action='store_true', default=False,
        help=(
            "Require verified manifests for manifest-aware fast paths; "
            "reject legacy/missing/mismatched ImpactSearch XLSX in strict mode."
        ),
    )
    p.add_argument('--output-format', choices=['xlsx','parquet','csv'], default=OUTPUT_FORMAT)
    # Parallelism across secondaries
    p.add_argument('--jobs', default=os.environ.get('STACKBUILDER_JOBS', '1'),
                   help="How many secondaries to build in parallel. Use an integer (e.g. 4) or 'auto'. Default: STACKBUILDER_JOBS env var or '1'.")
    return p.parse_args(argv)

def main(argv=None):
    args = parse_args(argv)
    global OUTPUT_FORMAT, SIGNAL_LIB_DIR_RUNTIME, VERBOSE, COMBINE_INTERSECTION
    OUTPUT_FORMAT = args.output_format
    SIGNAL_LIB_DIR_RUNTIME = args.signal_lib_dir
    VERBOSE = args.verbose
    COMBINE_INTERSECTION = (args.combine_mode == 'intersection')
    ensure_dir(args.outdir)
    secondaries = []
    if args.secondaries:
        secondaries = [s.strip() for s in args.secondaries.split(',') if s.strip()]
    elif args.secondary:
        secondaries = [args.secondary.strip()]
    else:
        # Default behavior: launch Dash UI when no arguments provided.
        # Phase 1B-2B: thread args.outdir through so the Dash callback
        # writes under the user-specified --outdir instead of hardcoded
        # RUNS_ROOT.
        run_dash(args.outdir, port=args.port)
        return

    # Parse primary tickers if provided
    specified_primaries = None
    if args.primaries:
        specified_primaries = [p.strip().upper() for p in args.primaries.split(',') if p.strip()]
        print(f"[INFO] Using specified primaries: {', '.join(specified_primaries)}")

    # Concurrency across independent secondaries. Parity: each secondary builds in isolation.
    jobs_arg = str(getattr(args, 'jobs', '1')).strip().lower()
    if jobs_arg == 'auto':
        max_workers = max(1, min(len(secondaries), (os.cpu_count() or 2)))
    else:
        try:
            max_workers = max(1, int(jobs_arg))
        except Exception:
            max_workers = 1
    if max_workers > 1 and len(secondaries) > 1:
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(run_for_secondary, args, sec, specified_primaries) for sec in secondaries]
            run_dirs = [f.result() for f in futs]
    else:
        run_dirs = [run_for_secondary(args, sec, specified_primaries) for sec in secondaries]
    if args.serve:
        run_dash(run_dirs[-1], port=args.port)

if __name__ == '__main__':
    main()