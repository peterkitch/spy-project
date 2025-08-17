"""
Yahoo Finance Batch Validator (Hybrid)
Uses yfinance for auth/transport, optimized to minimize data volume.
Strategy:
  1) Batch check via yf.download(period="5d", group_by="ticker") to cover many symbols at once.
  2) Fallback only for missing/ambiguous tickers with concurrent Ticker().history(period="1mo").
Determines 'active' via last bar date within STALE_DAYS.
"""
import re
import time
import concurrent.futures
import io
import contextlib
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import yfinance as yf

from global_ticker_library.gl_config import (
    BATCH_SIZE,
    INTER_BATCH_SLEEP,
    STALE_DAYS,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    RETRY_BACKOFF_MAX,
    VALID_SUFFIXES,
)

# To keep memory/network sane with yfinance, use a smaller effective batch for download:
_DL_BATCH = min(BATCH_SIZE, 100)
_FALLBACK_WORKERS = 8  # Threaded single-symbol history fetches (gentle)

class _SilentIO(io.StringIO):
    def write(self, *args, **kwargs):
        return 0

# Canonicalization patterns for Yahoo Finance
_CANON_PATTERNS = (
    # Class shares: BRK.B -> BRK-B
    (re.compile(r"^([A-Z0-9]+)\.([A-Z])$"), lambda m: f"{m.group(1)}-{m.group(2)}"),
    # Preferreds: JPM-D -> JPM-PD (but not if already has P)
    (re.compile(r"^([A-Z0-9]+)-([A-Z])$"), lambda m: f"{m.group(1)}-P{m.group(2)}" if not m.group(1).endswith('P') else m.group(0)),
    # Warrants/Rights/Units: .W/.U/.R -> -W/-U/-R
    (re.compile(r"^([A-Z0-9]+)\.(W|U|R)$"), lambda m: f"{m.group(1)}-{m.group(2)}"),
)

def canonicalize(sym: str) -> str:
    """Convert ticker to Yahoo Finance canonical form"""
    s = sym.strip().upper()
    
    # Check if symbol ends with a valid international suffix
    # If it does, preserve it as-is (don't transform)
    for suffix in VALID_SUFFIXES:
        if s.endswith(suffix):
            return s
    
    # Apply transformation patterns only if not a valid suffix
    for pat, fn in _CANON_PATTERNS:
        m = pat.match(s)
        if m:
            return fn(m)
    return s

def classify_error(exc: Optional[Exception], msg: str = "") -> str:
    """Classify exception/message into error codes"""
    name = exc.__class__.__name__ if exc else ""
    text = (msg or "").lower()
    
    if "too many requests" in text or "rate limit" in text or "429" in text:
        return "rate_limit"
    if "YFRateLimitError" in name:
        return "rate_limit"
    if "timed out" in text or "timeout" in text or "curl: (28)" in text:
        return "timeout"
    if "no data found" in text or "possibly delisted" in text or "no price data" in text:
        return "no_price_data"
    if "not found" in text or "404" in text or "symbol may be delisted" in text:
        return "not_found"
    if "connection" in text or "network" in text:
        return "timeout"
    return "other"


def _epoch_to_iso(ts: Optional[int]) -> Optional[str]:
    try:
        if ts is None:
            return None
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def _is_active_epoch(epoch_ts: Optional[int]) -> bool:
    if not epoch_ts:
        return False
    now = datetime.now(timezone.utc).timestamp()
    return (now - float(epoch_ts)) <= (STALE_DAYS * 86400)


def _to_utc_dt(dt_like) -> Optional[datetime]:
    if dt_like is None:
        return None
    try:
        ts = pd.Timestamp(dt_like)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.to_pydatetime().astimezone(timezone.utc)
    except Exception:
        return None


def _chunked(seq: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _result_invalid(sym: str, code: str = "not_found", msg: str = "") -> Dict:
    return {
        "symbol": sym,
        "exists": False,
        "active": False,
        "quoteType": None,
        "exchange": None,
        "currency": None,
        "regularMarketTime": None,
        "regularMarketTime_iso": None,
        "meta_exists": False,
        "has_price": False,
        "status": "invalid",
        "error_code": code,
        "error_msg": msg[:200] if msg else "",
    }


def _mk_result(sym: str, last_dt: Optional[pd.Timestamp]) -> Dict:
    """Return ACTIVE or STALE (if last_dt too old) with meta_exists/has_price set."""
    if last_dt is None:
        # No bars => no price data but metadata exists
        return {
            "symbol": sym,
            "exists": True,              # symbol path succeeded, but no bars
            "active": False,
            "quoteType": None,
            "exchange": None,
            "currency": None,
            "regularMarketTime": None,
            "regularMarketTime_iso": None,
            "meta_exists": True,         # metadata exists
            "has_price": False,          # but no price data
            "status": "stale",           # treat as STALE (metadata exists, no recent price)
        }
    if last_dt.tzinfo is None:
        last_dt = last_dt.tz_localize("UTC")
    epoch = int(last_dt.timestamp())
    return {
        "symbol": sym,
        "exists": True,
        "active": _is_active_epoch(epoch),
        "quoteType": None,
        "exchange": None,
        "currency": None,
        "regularMarketTime": epoch,
        "regularMarketTime_iso": last_dt.isoformat(timespec="seconds"),
        "meta_exists": True,
        "has_price": True,
        "status": "active" if _is_active_epoch(epoch) else "stale",
    }

def _unknown_record(sym: str, code: str, msg: str = "") -> Dict:
    return {
        "symbol": sym,
        "exists": False,             # Set to False for errors as per external help
        "meta_exists": False,        # unknown/error means no metadata
        "has_price": False,
        "active": False,
        "status": "unknown",
        "error_code": code,
        "error_msg": msg[:200],
        "quoteType": None, "exchange": None, "currency": None,
        "regularMarketTime": None,
        "regularMarketTime_iso": None,
    }


def _last_close_from_multi(df: pd.DataFrame, sym: str) -> Optional[pd.Timestamp]:
    """Handle MultiIndex columns when group_by='ticker'."""
    try:
        s = df[sym]["Close"]
        s = s.dropna()
        if not s.empty:
            ts = pd.Timestamp(s.index[-1])
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            return ts
    except Exception:
        return None
    return None


def _validate_batch_download(batch: List[str], threads: bool = True) -> Dict[str, Dict]:
    res: Dict[str, Dict] = {}
    try:
        with redirect_stdout(_SilentIO()), redirect_stderr(_SilentIO()):
            data = yf.download(
                batch, period="5d", interval="1d",
                group_by="ticker", auto_adjust=False, progress=False,
                threads=threads, prepost=False, rounding=False,
                raise_errors=False  # avoid exceptions; we'll fallback/mark unknown
            )
    except Exception:
        return res  # let fallback handle all

    # Single-symbol returned as a simple DataFrame
    if isinstance(batch, list) and len(batch) == 1 and isinstance(data, pd.DataFrame):
        sym = batch[0]
        try:
            s = data.get("Close", pd.Series()).dropna()
            last_dt = s.index[-1] if not s.empty else None
            if last_dt is not None:
                res[sym] = _mk_result(sym, last_dt)
        except Exception:
            pass
        return res

    # MultiIndex case
    if isinstance(data, pd.DataFrame) and isinstance(data.columns, pd.MultiIndex):
        for sym in batch:
            try:
                s = data[sym]["Close"].dropna()
                last_dt = s.index[-1] if not s.empty else None
                if last_dt is not None:
                    res[sym] = _mk_result(sym, last_dt)
            except Exception:
                # Not present -> let fallback handle
                pass
    return res


def _fallback_single_history(sym: str) -> Dict:
    delay = RETRY_BACKOFF_BASE
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with redirect_stdout(_SilentIO()), redirect_stderr(_SilentIO()):
                hist = yf.Ticker(sym).history(period="1mo", interval="1d",
                                              auto_adjust=False, prepost=False, actions=False)
            if isinstance(hist, pd.DataFrame) and not hist.empty and "Close" in hist.columns:
                s = hist["Close"].dropna()
                last_dt = s.index[-1] if not s.empty else None
                if last_dt is not None:
                    return _mk_result(sym, last_dt)
                # no non-NaN closes
                return {
                    "symbol": sym,
                    "exists": True, "meta_exists": True, "has_price": False,
                    "active": False, "status": "stale",
                    "error_code": "no_price_data", "error_msg": "",
                    "quoteType": None, "exchange": None, "currency": None,
                    "regularMarketTime": None, "regularMarketTime_iso": None,
                }
            # Empty frame
            return {
                "symbol": sym,
                "exists": True, "meta_exists": True, "has_price": False,
                "active": False, "status": "stale",
                "error_code": "no_price_data", "error_msg": "",
                "quoteType": None, "exchange": None, "currency": None,
                "regularMarketTime": None, "regularMarketTime_iso": None,
            }
        except Exception as e:
            code = classify_error(e, str(e))
            if attempt == MAX_RETRIES:
                # Only transient rate/time errors -> unknown; else leave as unknown to retry with backoff
                return _unknown_record(sym, code, str(e))
            time.sleep(min(delay, RETRY_BACKOFF_MAX))
            delay *= 2
    return _unknown_record(sym, "other", "max retries")


def validate_symbols(symbols: List[str], gentle: bool = False, progress: bool = False, 
                    timeout: int = 10, session: Optional[object] = None) -> Tuple[List[Dict], Dict[str, int]]:
    """
    Hybrid validator with structured error reporting
    Args:
        symbols: List of ticker symbols to validate
        gentle: If True, use lower concurrency and add delays
        progress: If True, show progress (deprecated, always False)
        timeout: Request timeout in seconds
    Returns:
        (results, aggregates) where results is list of dicts with structured validation data
        and aggregates is error count summary
    """
    # Canonicalize and track originals
    symbol_map = {}  # canonical -> original
    canonical_symbols = []
    
    for s in symbols:
        if s and s.strip():
            original = s.strip().upper()
            canonical = canonicalize(original)
            symbol_map[canonical] = original
            canonical_symbols.append(canonical)
    
    # De-dupe while preserving order
    seen, ordered = set(), []
    for s in canonical_symbols:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    
    results: Dict[str, Dict] = {}
    aggregates = {"rate_limit": 0, "timeout": 0, "no_price_data": 0, "not_found": 0, "other": 0}
    
    # Adjust batch size and workers for gentle mode
    batch_size = _DL_BATCH if not gentle else min(50, _DL_BATCH)
    workers = _FALLBACK_WORKERS if not gentle else 4
    
    # Batch pass with no progress display
    batches = list(_chunked(ordered, batch_size))
    for i, batch in enumerate(batches):
        try:
            partial = _validate_batch_download(batch, threads=not gentle)
            results.update(partial)
        except Exception as e:
            code = classify_error(e, str(e))
            aggregates[code] = aggregates.get(code, 0) + len(batch)
            for sym in batch:
                results[sym] = _unknown_record(sym, code, str(e))
        
        # Fallback for misses
        misses = [sym for sym in batch if sym not in results]
        if misses:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_fallback_single_history, sym): sym for sym in misses}
                # Use individual future timeouts instead of as_completed timeout
                for future in concurrent.futures.as_completed(futures):
                    sym = futures[future]
                    try:
                        # Apply timeout to individual future.result() call
                        out = future.result(timeout=timeout)
                        # track aggregates by error_code if present
                        code = out.get("error_code")
                        if code:
                            aggregates[code] = aggregates.get(code, 0) + 1
                        results[sym] = out
                    except Exception as e:
                        error_code = classify_error(e, str(e))
                        aggregates[error_code] = aggregates.get(error_code, 0) + 1
                        results[sym] = {
                            "symbol": sym,
                            "original": symbol_map.get(sym, sym),
                            "status": "unknown",
                            "error_code": error_code,
                            "error_msg": str(e)[:200],
                            "meta_exists": False,
                            "has_price": False,
                            "exists": False,
                        }
        
        # Pacing
        if gentle and i < len(batches) - 1:
            time.sleep(0.2)  # Extra delay in gentle mode
        elif i < len(batches) - 1:
            time.sleep(INTER_BATCH_SLEEP)
    
    # Attach canonical/original and ensure status present
    final_results: List[Dict] = []
    for sym in ordered:
        r = results.get(sym) or _unknown_record(sym, "other", "no result")
        r["symbol"] = sym
        r["original"] = symbol_map.get(sym, sym)
        if "status" not in r or r["status"] not in ("active","stale","invalid","unknown"):
            # default to unknown if something slipped through
            r = {**r, **_unknown_record(sym, r.get("error_code","other"), r.get("error_msg",""))}
        final_results.append(r)

    return final_results, aggregates