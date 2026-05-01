#!/usr/bin/env python3
# PRJCT9 - stackbuilder.py
# Lean fastpath: uses existing Signal Library only; no network; optional minimal Dash UI.
# Phases: 1) Preflight  2) Rank All  3) Stack Builder

import os, re, json, math, glob, argparse, time, shutil, threading
from types import SimpleNamespace
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any, Union
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
DEFAULT_GRACE_DAYS = int(os.environ.get('IMPACT_CALENDAR_GRACE_DAYS', '7') or 7)
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
def _fetch_secondary_from_yf(secondary: str, price_basis: str) -> pd.DataFrame:
    if yf is None:
        raise RuntimeError("yfinance not installed. Install with: pip install yfinance")
    sym = (secondary or "").upper()  # keep caret for indices like ^VIX
    df = yf.download(sym, period="max", interval="1d", auto_adjust=False, progress=False, threads=True)
    if df is None or len(df) == 0:
        raise RuntimeError(f"yfinance returned no data for {sym}")
    df = df.rename_axis('Date').reset_index().set_index('Date')
    # tz-naive index
    df.index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(None) if getattr(pd.Timestamp(d), "tz", None) else pd.Timestamp(d) for d in df.index])

    # Flatten MultiIndex columns if present (yfinance sometimes returns this)
    if isinstance(df.columns, pd.MultiIndex):
        # For MultiIndex, take the first level
        df.columns = df.columns.get_level_values(0)

    # Now find the close columns with simplified logic
    close_col = None
    adj_close_col = None
    for col in df.columns:
        col_lower = str(col).lower()
        if col_lower == 'close':
            close_col = col
        elif col_lower == 'adj close':
            adj_close_col = col

    if price_basis.lower().startswith('adj') and adj_close_col:
        out = pd.DataFrame(df[adj_close_col]).rename(columns={adj_close_col: 'Close'})
    elif close_col:
        out = pd.DataFrame(df[close_col]).rename(columns={close_col: 'Close'})
    else:
        raise RuntimeError(f"yfinance returned no Close column for {sym}. Columns: {list(df.columns)}")

    return out.astype(FLOAT_DTYPE)

def load_secondary_prices(secondary: str, price_basis: str) -> pd.DataFrame:
    sec = (secondary or "").upper()
    # Also try without caret for index symbols like ^VIX -> VIX
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
            # ensure tz-naive index to match signal library
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            else:
                df.index = pd.DatetimeIndex([pd.Timestamp(d).tz_localize(None) if getattr(pd.Timestamp(d), "tz", None) else pd.Timestamp(d) for d in df.index])
            df = df.sort_index()
            if price_basis.lower().startswith('adj') and 'Adj Close' in df.columns:
                out = pd.DataFrame(df['Adj Close']).rename(columns={'Adj Close':'Close'})
            elif 'Close' in df.columns:
                out = pd.DataFrame(df['Close'])
            else:
                raise ValueError(f"{p}: missing Close/Adj Close")
            out = out[~out.index.duplicated(keep='last')].astype(FLOAT_DTYPE)
            return out
    # No cache -> fetch once from yfinance (do not persist)
    return _fetch_secondary_from_yf(sec, price_basis)

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

def try_load_rank_from_impact_xlsx(sec: str, dirpath: str, max_age_days: int) -> Optional[pd.DataFrame]:
    """Load ImpactSearch Excel ONLY if it matches the selected secondary ticker."""
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
            return None

        df = pd.read_excel(best, engine="openpyxl")
        df = _standardize_rank_columns(df)
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
    for p in list_signal_library_candidates(ticker):
        try:
            import pickle
            with open(p, 'rb') as f:
                return pickle.load(f)
        except Exception:
            continue
    return None

def load_lib_or_none(t: str) -> Optional[dict]:
    if load_signal_library:
        try:
            lib = load_signal_library(t)
            if lib:
                return lib
        except Exception:
            pass
    return fallback_load_signal_library(t)

# ---------- Signal application and metrics ----------
def apply_signals_to_secondary(primary_signals: List[str], primary_dates: List, sec_returns: pd.Series) -> pd.Series:
    """
    ImpactSearch-parity alignment:
      - align to secondary index
      - carry forward signals within a grace window (default 7 calendar days)
      - fill missing with 'None'
    """
    if not primary_signals or not primary_dates or sec_returns.empty:
        return pd.Series(dtype=FLOAT_DTYPE)

    # Normalize indices (drop tz if present)
    idx = pd.DatetimeIndex(pd.to_datetime(primary_dates))
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    sidx = sec_returns.index
    if getattr(sidx, "tz", None) is not None:
        sidx = sidx.tz_localize(None)

    sigs = pd.Series(list(primary_signals), index=idx)

    # Use configured grace window (default 7 days like ImpactSearch)
    grace_days = DEFAULT_GRACE_DAYS
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
    return captures.fillna(0.0)

def metrics_from_captures(captures: pd.Series) -> Optional[Dict[str, float]]:
    if captures.empty:
        return None
    mask = captures.ne(0.0)
    n = int(mask.sum())
    if n == 0:
        return None
    vals = captures[mask].astype(FLOAT_DTYPE)
    wins = int((vals > 0).sum())
    losses = n - wins
    win_ratio = (wins / n * 100.0) if n else 0.0
    avg = float(vals.mean())
    total = float(vals.sum())
    std = float(vals.std(ddof=1)) if n > 1 else 0.0
    if n > 1 and std != 0.0:
        annual_ret = avg * 252.0
        annual_std = std * math.sqrt(252.0)
        sharpe = (annual_ret - RISK_FREE_ANNUAL) / annual_std if annual_std != 0 else 0.0
        t_stat = avg / (std / math.sqrt(n))
        p_val = float(2 * (1 - stats.t.cdf(abs(t_stat), df=n - 1)))
    else:
        sharpe, t_stat, p_val = 0.0, None, None
    # keep full-precision for gating; present rounded in tables
    out = {
        'Trigger Days': n,
        'Wins': wins,
        'Losses': losses,
        'Win Ratio (%)': round(win_ratio, 2),
        'Std Dev (%)': round(std, 4),
        'Sharpe Ratio': round(sharpe, 2),
        'Avg Daily Capture (%)': round(avg, 4),
        'Total Capture (%)': round(total, 4),
        't-Statistic': round(t_stat, 4) if t_stat is not None else 'N/A',
        'p-Value': round(p_val, 4) if p_val is not None else 'N/A',
        'Significant 90%': 'Yes' if p_val is not None and p_val < 0.10 else 'No',
        'Significant 95%': 'Yes' if p_val is not None and p_val < 0.05 else 'No',
        'Significant 99%': 'Yes' if p_val is not None and p_val < 0.01 else 'No',
        # raw fields for gating/ranking
        'Sharpe_raw': float(sharpe),
        'Avg_raw': float(avg),
        'Total_raw': float(total),
        'p_raw': float(p_val) if p_val is not None else None,
    }
    return out

# ---------- Phase 1: Preflight ----------
def phase1_preflight(args, secondary: str, specified_primaries: Optional[List[str]] = None):
    vendor_secondary, _ = resolve_symbol(secondary)
    sec_df = load_secondary_prices(vendor_secondary, args.price_basis)
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
def _score_primary(primary: str, sec_rets: pd.Series) -> Optional[Dict]:
    vendor, _ = resolve_symbol(primary)
    lib = load_lib_or_none(vendor)
    if not lib:
        print(f"[WARN] No signal library found for {vendor}")
        return None
    sigs = lib.get('primary_signals') or lib.get('primary_signals_int8')
    dates = lib.get('dates') or lib.get('date_index')
    if not sigs or not dates:
        return None
    if isinstance(sigs[0], (int, np.integer)):
        dec = {1:'Buy', -1:'Short', 0:'None'}
        sigs = [dec.get(int(x), 'None') for x in sigs]
    caps = apply_signals_to_secondary(sigs, dates, sec_rets)
    m = metrics_from_captures(caps)
    if not m:  # Just check if we have metrics, not the trigger count
        return None
    # Log low trigger days as a warning
    if m['Trigger Days'] <= 100:
        print(f"[WARN] {vendor} has only {m['Trigger Days']} trigger days against secondary")
    m['Primary Ticker'] = vendor
    return m

def phase2_rank_all(args, primaries_df: pd.DataFrame, sec_rets: pd.Series, outdir: str, secondary: Optional[str] = None, progress_path: Optional[str] = None):
    """Phase 2: Rank all primaries against secondary with progress tracking."""
    # Fast-path: use ImpactSearch .xlsx if requested and available
    if getattr(args, "prefer_impact_xlsx", False):
        sec = (secondary or getattr(args, "secondary", "") or "").upper()
        rank_all = try_load_rank_from_impact_xlsx(
            sec=sec,
            dirpath=getattr(args, "impact_xlsx_dir", DEFAULT_IMPACT_XLSX_DIR),
            max_age_days=int(getattr(args, "impact_xlsx_max_age_days", 45))
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
            rank_inverse = rank_all.copy()
            for col in ['Avg Daily Capture (%)','Total Capture (%)','Sharpe Ratio']:
                if col in rank_inverse.columns:
                    rank_inverse[col] = pd.to_numeric(rank_inverse[col], errors='coerce') * -1.0
            rank_inverse = rank_inverse.sort_values(by='Total Capture (%)', ascending=False).reset_index(drop=True)
            write_table(rank_all, os.path.join(outdir, 'rank_all'))
            write_table(rank_direct, os.path.join(outdir, 'rank_direct'))
            write_table(rank_inverse, os.path.join(outdir, 'rank_inverse'))
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
            # Do NOT compute 70k+ primaries if user asked for fast-path
            raise RuntimeError(f"No ImpactSearch Excel found for secondary '{secondary or args.secondary}' in "
                               f"{getattr(args,'impact_xlsx_dir', DEFAULT_IMPACT_XLSX_DIR)}. "
                               f"Expected a file like '{secondary or args.secondary}_analysis.xlsx'. "
                               f"Uncheck 'Use ImpactSearch .xlsx' to compute from signal libraries.")
    max_workers = None if args.threads == 'auto' else int(args.threads)
    rows = []
    missing = []
    total = len(primaries_df['Primary Ticker'])

    if VERBOSE:
        print(f"[PHASE2] Scoring {total} primary tickers against {secondary}...")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_score_primary, t, sec_rets): t for t in primaries_df['Primary Ticker']}

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
            res = fut.result()
            if res:
                rows.append(res)
            else:
                missing.append(ticker)

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

    if not rows:
        raise SystemExit("[FATAL] No primaries produced valid metrics.")

    rank_all = pd.DataFrame(rows)
    rank_direct = rank_all.sort_values(by='Total Capture (%)', ascending=False).reset_index(drop=True)

    rank_inverse = rank_all.copy()
    # flip sign for ranking; p-Value and Trigger Days unchanged
    for col in ['Avg Daily Capture (%)','Total Capture (%)','Sharpe Ratio']:
        rank_inverse[col] = rank_inverse[col].astype(float) * -1.0
    rank_inverse = rank_inverse.sort_values(by='Total Capture (%)', ascending=False).reset_index(drop=True)

    write_table(rank_all, os.path.join(outdir, 'rank_all'))
    write_table(rank_direct, os.path.join(outdir, 'rank_direct'))
    write_table(rank_inverse, os.path.join(outdir, 'rank_inverse'))
    return rank_all, rank_direct, rank_inverse

# ---------- Phase 3: Stack Builder ----------
def _captures_for(primary: str, mode: str, sec_rets: pd.Series) -> pd.Series:
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
    caps = apply_signals_to_secondary(sigs, dates, sec_rets)
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
def _signals_aligned_and_mask(primary: str, mode: str, sec_index: pd.DatetimeIndex) -> Tuple[pd.Series, pd.Series]:
    """Return (signals_aligned_to_sec_index, present_mask_before_fill)."""
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
    except Exception as e:
        print(f"[ERROR] Failed to load signals for {vendor}: {e}")
        raise RuntimeError(f"Ticker {vendor} signal library error: {e}") from e
    grace_days = int(os.environ.get('IMPACT_CALENDAR_GRACE_DAYS', '0') or 0)
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
    """Allow Buy/NONE or Short/NONE; cancel to NONE on any Buy+Short mix."""
    if not members:
        return pd.Series(dtype=object)
    df = pd.concat(members, axis=1).fillna('None').astype(str)
    mapper = {'Buy': 1, 'Short': -1}
    # fast vectorized combine
    arr = np.stack([df[c].map(mapper).fillna(0).astype(np.int8).to_numpy(dtype=np.int8) for c in df.columns], axis=1)
    nz = (arr != 0)
    cnt = nz.sum(axis=1)
    ssum = arr.sum(axis=1)
    out = np.where(cnt == 0, 'None',
                   np.where(ssum == cnt, 'Buy',
                            np.where(ssum == -cnt, 'Short', 'None')))
    return pd.Series(out, index=df.index)

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
    m = metrics_from_captures(combined_caps)
    return combined_caps, m

def phase3_build_stacks(args, rank_direct: pd.DataFrame, rank_inverse: pd.DataFrame, sec_rets: pd.Series, outdir: str, progress_cb=None) -> Tuple[pd.DataFrame, List]:
    """Beam+exhaustive search over top/bottom cohort with both modes.
    progress_cb: optional callback for reporting progress
    Returns: (leaderboard_df, final_members_list)
    """
    topN, botN = int(args.top_n), int(args.bottom_n)
    min_td = int(getattr(args, 'min_trigger_days', 30))
    eps = float(getattr(args, 'sharpe_eps', 1e-6))
    seed_by = getattr(args, 'seed_by', 'sharpe')
    optimize_by = getattr(args, 'optimize_by', seed_by)  # Default to seed_by if not specified
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
            sig_cache[(t, m)] = _signals_aligned_and_mask(t, m, sec_rets.index)
    def sigs_for(t, m): return sig_cache.get((t, m), (pd.Series('None', index=sec_rets.index),
                                                        pd.Series(False, index=sec_rets.index)))

    # 3) Build singles (K=1) that pass min trigger days
    singles = []
    for _, r in cohort.iterrows():
        t, m = r['Primary Ticker'], r['Mode']
        sig_pair = sigs_for(t, m)
        _, met = _combined_metrics_signals([sig_pair], sec_rets)
        if met and int(met['Trigger Days']) >= min_td:
            # add raw fields for stable sorting
            met['Sharpe_raw'] = float(met['Sharpe Ratio'])
            met['Total_raw']  = float(met['Total Capture (%)'])
            met['p_raw']      = (float(met['p-Value']) if met['p-Value'] != 'N/A' else None)
            singles.append(((t, m, sig_pair), met))
    if not singles:
        raise SystemExit("[FATAL] No single candidate passed the min Trigger Days gate.")

    key_sharpe = lambda it: (it[1]['Sharpe_raw'], it[1]['Total_raw'], -(it[1]['p_raw'] if it[1]['p_raw'] is not None else 1.0), it[0][0])
    key_total  = lambda it: (it[1]['Total_raw'],  it[1]['Sharpe_raw'], -(it[1]['p_raw'] if it[1]['p_raw'] is not None else 1.0), it[0][0])
    singles.sort(key=(key_sharpe if seed_by == 'sharpe' else key_total), reverse=True)

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
                    continue
                # Enforce monotone improvement based on optimize_by metric (unless allow_decreasing is enabled)
                if not allow_decreasing:
                    if optimize_by == 'total_capture':
                        prev_metric = leaderboard[-1]['Total Capture (%)']
                        cur_metric = mm['Total_raw']
                        metric_name = 'Total Capture'
                        if float(cur_metric) <= float(prev_metric) + eps:
                            search_stats['combinations_rejected'] += 1
                            search_stats['rejection_reasons']['sharpe_improvement'] += 1  # reuse counter
                            if VERBOSE:
                                print(f"  [REJECT] {[t for t in tickers]}: {metric_name} {cur_metric:.4f}% <= {prev_metric:.4f}% + {eps}")
                            continue
                    else:  # optimize_by == 'sharpe'
                        prev_metric = leaderboard[-1]['Sharpe Ratio']
                        cur_metric = mm['Sharpe_raw']
                        metric_name = 'Sharpe'
                        if float(cur_metric) <= float(prev_metric) + eps:
                            search_stats['combinations_rejected'] += 1
                            search_stats['rejection_reasons']['sharpe_improvement'] += 1
                            if VERBOSE:
                                print(f"  [REJECT] {[t for t in tickers]}: {metric_name} {cur_metric:.4f} <= {prev_metric:.4f} + {eps}")
                            continue
                # Set key for comparison (always needed, regardless of allow_decreasing)
                if optimize_by == 'total_capture':
                    key = (mm['Total_raw'], mm['Sharpe_raw'], - (mm['p_raw'] if mm['p_raw'] is not None else 1.0))
                else:  # optimize_by == 'sharpe'
                    key = (mm['Sharpe_raw'], - (mm['p_raw'] if mm['p_raw'] is not None else 1.0), mm['Total_raw'])
                if (best is None) or (key > best[0]):
                    best = (key, path, comb, mm)
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
            # Beam expand
            cand_states = []
            seen = set()
            for path, _, prevm in beam:
                prev_metric = float(prevm['Sharpe_raw'] if optimize_by=='sharpe' else prevm['Total_raw'])
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
                        continue
                    # Enforce monotone improvement (unless allow_decreasing is enabled)
                    if not allow_decreasing:
                        cur_metric = float(m2['Sharpe_raw'] if optimize_by=='sharpe' else m2['Total_raw'])
                        if cur_metric <= prev_metric + eps:
                            search_stats['combinations_rejected'] += 1
                            search_stats['rejection_reasons']['sharpe_improvement'] += 1
                            continue
                    key = (m2['Sharpe_raw'] if optimize_by=='sharpe' else m2['Total_raw'],
                           - (m2['p_raw'] if m2['p_raw'] is not None else 1.0))
                    sig = (tuple(sorted([x[0] for x in new_path])), tuple([x[1] for x in new_path]))
                    if sig in seen: continue
                    seen.add(sig)
                    cand_states.append((key, new_path, comb2, m2))
            if cand_states:
                cand_states.sort(key=lambda x: x[0], reverse=True)
                beam = [(p, c, m) for _, p, c, m in cand_states[:beam_w]]
                # choose best of current K
                _, best_path, best_comb, best_met = cand_states[0]
                found = (None, best_path, best_comb, best_met)

        if not found:
            if k_patience > 0 and patience_used < k_patience:
                patience_used += 1
                metric_name = 'Total Capture' if optimize_by == 'total_capture' else 'Sharpe'
                print(f"[PHASE3] K={K}: No {metric_name} improvement (>={eps:.6f}), using patience {patience_used}/{k_patience}")
                continue  # Continue to next K instead of breaking
            else:
                metric_name = 'Total Capture' if optimize_by == 'total_capture' else 'Sharpe'
                if allow_decreasing:
                    print(f"[PHASE3] Stopping at K={K-1}: No valid candidates with >={min_td} trigger days")
                else:
                    print(f"[PHASE3] Stopping at K={K-1}: No candidate improves {metric_name} by >{eps:.6f} with >={min_td} trigger days")
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
            html.Label("Seed by"),
            dcc.RadioItems(
                id='seed-by',
                options=[{'label':'Sharpe','value':'sharpe'},
                         {'label':'Total Capture','value':'total_capture'}],
                value='total_capture',
                labelStyle={'display':'inline-block','marginRight':'12px'},
                style={'display':'inline-block','marginRight':'12px'}
            ),
            html.Label("Optimize by"),
            dcc.RadioItems(
                id='optimize-by',
                options=[{'label':'Sharpe','value':'sharpe'},
                         {'label':'Total Capture','value':'total_capture'},
                         {'label':'(Same as Seed)','value':'auto'}],
                value='auto',
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
        seed_by_val = seed_by if seed_by else 'total_capture'
        optimize_by_val = optimize_by if optimize_by and optimize_by != 'auto' else seed_by_val
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

            args = SimpleNamespace(
                secondary=sec, secondaries=None, primaries=None,
                top_n=int(topn or 20), bottom_n=int(bottomn or 20), max_k=int(maxk or 6),
                alpha=float(alpha or 0.05), min_marginal_capture=0.0,
                threads='auto', outdir=RUNS_ROOT, price_basis='adj',
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

            def _job():
                try:
                    run_for_secondary(args, sec, specified_primaries=primaries if primaries else None)
                except BaseException as e:
                    import traceback
                    # Extract ticker name from error message if available
                    error_msg = str(e)
                    full_trace = traceback.format_exc()
                    print(f"[ERROR] Job failed for {sec}:\n{full_trace}")
                    _write_progress(ppath, status='failed', phase='error', percent=100.0,
                                    message=f"Error for {sec}: {e.__class__.__name__}: {error_msg}")

            threading.Thread(target=_job, daemon=True).start()

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
def run_for_secondary(args, secondary: str, specified_primaries: Optional[List[str]] = None) -> str:
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
    # Enforce strict calendar by default for parity with spymaster
    os.environ['IMPACT_CALENDAR_GRACE_DAYS'] = str(getattr(args, 'grace_days', 0) or 0)
    primaries_df, sec_rets, vendor_secondary = phase1_preflight(args, secondary, specified_primaries)
    ts = now_ts()
    # Clean secondary name for filesystem, but preserve '^' (safe on NTFS) per design
    vendor_secondary_clean = vendor_secondary.replace(".", "_")
    # Create parent directory for this secondary ticker
    secondary_parent = os.path.join(RUNS_ROOT, vendor_secondary_clean)
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

    try:
        manifest = {
            'secondary': vendor_secondary,
            'started_at': datetime.now().isoformat(),
            'params': {
                'alpha': args.alpha,
                'max_k': args.max_k,
                'top_n': args.top_n,
                'bottom_n': args.bottom_n,
                'price_basis': args.price_basis,
                'min_trigger_days': getattr(args, 'min_trigger_days', 30),
                'sharpe_eps': getattr(args, 'sharpe_eps', 0.01),
                'seed_by': getattr(args, 'seed_by', 'total_capture')
            }
        }
        write_json(os.path.join(temp_outdir, 'run_manifest.json'), manifest)

        _write_progress(ppath, status='running', phase='ranking', percent=25.0,
                        message=f'Ranking {len(primaries_df)} primaries against {vendor_secondary}...',
                        outdir=temp_outdir, secondary=vendor_secondary)
        rank_all, rank_direct, rank_inverse = phase2_rank_all(args, primaries_df, sec_rets, temp_outdir, secondary=vendor_secondary, progress_path=ppath)

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
            args, rank_direct, rank_inverse, sec_rets, temp_outdir, progress_cb=_k_progress
        )

        _write_progress(ppath, status='running', phase='finalizing', percent=90.0,
                        message='Finalizing results and generating output files...',
                        outdir=temp_outdir, secondary=vendor_secondary)

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
            seed_tag = "seedS" if getattr(args, 'seed_by', 'sharpe') == 'sharpe' else "seedTC"
            final_name = f"{vendor_secondary_clean}__{seed_tag}__{('_'.join(member_names))}"
            # Truncate if too long for filesystem (Windows limit is 260 chars for full path)
            if len(final_name) > 200:
                final_name = final_name[:197] + "..."
        else:
            # No stack was built - use special name
            seed_tag = "seedS" if getattr(args, 'seed_by', 'sharpe') == 'sharpe' else "seedTC"
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
        manifest['outputs'] = {
            'rank_all': f'rank_all.{ext}',
            'rank_direct': f'rank_direct.{ext}',
            'rank_inverse': f'rank_inverse.{ext}',
            'cohort': f'cohort.{ext}',
            'leaderboard': f'combo_leaderboard.{ext}'
        }
        write_json(os.path.join(final_outdir, 'run_manifest.json'), manifest)
        print(f"[COMPLETE] Secondary {vendor_secondary} finished in {elapsed_time:.1f}s")
        print(f"[RESULT] Best stack K={len(final_members)}: Sharpe={summary['best_sharpe']:.3f}, Capture={summary['best_capture']:.2f}%, TD={summary['best_trigger_days']}")
        print(f"[OUTPUT] Results saved to: {final_outdir}")
        return final_outdir

    except Exception as e:
        # Clean up temp directory on failure
        if os.path.exists(temp_outdir):
            shutil.rmtree(temp_outdir, ignore_errors=True)
            print(f"[CLEANUP] Removed temp directory: {temp_outdir}")
        raise

def parse_args(argv=None):
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
    p.add_argument('--seed-by', choices=['sharpe','total_capture'], default='total_capture',
                   help='Metric to choose the initial K=1 seed')
    p.add_argument('--optimize-by', choices=['sharpe','total_capture'], default=None,
                   help='Metric to optimize for K>=2 (defaults to seed-by if not specified)')
    p.add_argument('--allow-decreasing', action='store_true',
                   help='Allow metric to decrease across K levels (find best at each K independently)')
    p.add_argument('--grace-days', type=int, default=0,
                   help='Max calendar pad when aligning primary signals to secondary (0 = strict, spymaster-parity)')
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
    p.add_argument('--price-basis', default='adj', choices=['adj','close'])
    p.add_argument('--fail-on-missing-cache', action='store_true')
    p.add_argument('--serve', action='store_true', help='Start minimal Dash to display results')
    p.add_argument('--port', type=int, default=8054)
    # ImpactSearch Excel + output format
    p.add_argument('--prefer-impact-xlsx', action='store_true', help='Prefer ImpactSearch .xlsx ranking if available')
    p.add_argument('--impact-xlsx-dir', default=DEFAULT_IMPACT_XLSX_DIR, help='Folder with ImpactSearch .xlsx exports')
    p.add_argument('--impact-xlsx-max-age-days', type=int, default=45)
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
        # Default behavior: launch Dash UI when no arguments provided
        run_dash(None, port=args.port)
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