"""
Yahoo Finance Batch Validator (Hybrid)
Uses yfinance for auth/transport, optimized to minimize data volume.
Strategy:
  1) Batch check via yf.download(period="5d", group_by="ticker") to cover many symbols at once.
  2) Fallback only for missing/ambiguous tickers with concurrent Ticker().history(period="1mo").
Determines 'active' via last bar date within STALE_DAYS.
"""
import os
import re
import time
import concurrent.futures
import io
import contextlib
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Optional, Tuple
import itertools

import pandas as pd
import yfinance as yf
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from global_ticker_library.gl_config import (
    BATCH_SIZE,
    INTER_BATCH_SLEEP,
    STALE_DAYS,
    STALE_DAYS_BY_TYPE,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    RETRY_BACKOFF_MAX,
    VALID_SUFFIXES,
    QUOTE_URL,
    QUOTE_BATCH_SIZE,
    DEFAULT_UA,
    REQUEST_TIMEOUT,
)

# To keep memory/network sane with yfinance, use a smaller effective batch for download:
_DL_BATCH = min(BATCH_SIZE, 100)
_FALLBACK_WORKERS = 8  # Threaded single-symbol history fetches (gentle)

# --- watchdog and pool timeouts ---
_DL_BATCH_TIMEOUT_S = 75              # hard cap for a single yf.download batch
_HIST_POOL_MIN_S = 30                 # minimum time we let a pool run
_HIST_POOL_MAX_S = 240                # maximum time we let a pool run

# Enable/disable quote phase via environment variable
ENABLE_QUOTE_PHASE = os.getenv("YF_ENABLE_QUOTE_PHASE", "0") == "1"

# Dynamic throttling system
class DynamicThrottler:
    """Dynamically adjusts batch size and delays based on success/failure rates"""
    def __init__(self, initial_batch=50, initial_delay=0.5):
        self.batch_size = initial_batch
        self.delay = initial_delay
        self.success_streak = 0
        self.failure_streak = 0
        self.total_successes = 0
        self.total_failures = 0
        self.last_rate_limit = None
        
    def on_success(self, batch_size=None):
        """Called when a batch succeeds"""
        self.success_streak += 1
        self.failure_streak = 0
        self.total_successes += batch_size or self.batch_size
        
        # Gradually increase efficiency after consecutive successes
        if self.success_streak >= 5:
            old_batch = self.batch_size
            old_delay = self.delay
            self.batch_size = min(int(self.batch_size * 1.2), QUOTE_BATCH_SIZE)  # Increase by 20%, cap at max
            self.delay = max(self.delay * 0.9, 0.1)  # Reduce delay by 10%, min 0.1s
            self.success_streak = 0  # Reset counter
            print(f"Throttler: Increasing efficiency - batch {old_batch}->{self.batch_size}, delay {old_delay:.1f}->{self.delay:.1f}s")
            
    def on_rate_limit(self):
        """Called when hitting rate limit"""
        self.failure_streak += 1
        self.success_streak = 0
        self.total_failures += self.batch_size
        self.last_rate_limit = time.time()
        
        old_batch = self.batch_size
        old_delay = self.delay
        
        # Back off aggressively
        self.batch_size = max(int(self.batch_size * 0.5), 10)  # Halve batch size, min 10
        self.delay = min(self.delay * 2, 30)  # Double delay, max 30s
        
        print(f"Throttler: Rate limit hit! Backing off - batch {old_batch}->{self.batch_size}, delay {old_delay:.1f}->{self.delay:.1f}s")
        
        # Exponential cooldown based on failure streak
        if self.failure_streak >= 3:
            cooldown = min(60 * self.failure_streak, 300)  # Max 5 min
            print(f"Throttler: Multiple failures ({self.failure_streak}), cooling down for {cooldown}s...")
            time.sleep(cooldown)
            
    def get_batch_size(self):
        """Get current batch size"""
        return self.batch_size
        
    def get_delay(self):
        """Get current delay"""
        return self.delay
        
    def get_stats(self):
        """Get throttling statistics"""
        return {
            "batch_size": self.batch_size,
            "delay": self.delay,
            "success_streak": self.success_streak,
            "failure_streak": self.failure_streak,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
        }

class _SilentIO(io.StringIO):
    def write(self, *args, **kwargs):
        return 0

# Helper functions for type-aware staleness
def _stale_days_for(qtype: Optional[str]) -> int:
    """Get staleness threshold for a given quote type"""
    if not qtype:
        return STALE_DAYS
    return STALE_DAYS_BY_TYPE.get(qtype.upper(), STALE_DAYS)

def _make_http_session() -> requests.Session:
    """Create HTTP session with retry logic"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json",
        "Referer": "https://finance.yahoo.com/"
    })
    retry = Retry(
        total=3,
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s

# Canonicalization patterns for Yahoo Finance
# No canonicalization - symbols are validated exactly as entered (uppercase only)

def classify_error(exc: Optional[Exception], msg: str = "") -> str:
    """Classify exception/message into error codes"""
    name = exc.__class__.__name__ if exc else ""
    text = (msg or "").lower()
    
    # Check for 404 errors FIRST (invalid symbols)
    if "404" in text or "http error 404" in text:
        return "not_found"
    
    # Only mark as rate_limit for actual 429 codes (and ensure not misreading 404)
    if "429" in text and "404" not in text:
        return "rate_limit"
    if "too many requests" in text:
        return "rate_limit"
    if "YFRateLimitError" in name:
        return "rate_limit"
    
    # Handle timeout errors
    if "timed out" in text or "timeout" in text or "curl: (28)" in text:
        return "timeout"
    
    # Handle clear delisting messages
    if "symbol may be delisted" in text or "no data found, symbol may be delisted" in text:
        return "not_found"
    if "no data found" in text or "possibly delisted" in text or "no price data" in text:
        return "no_price_data"
    
    # Network issues
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


def _is_active_epoch(epoch_ts: Optional[int], qtype: Optional[str] = None) -> bool:
    if not epoch_ts:
        return False
    now = datetime.now(timezone.utc).timestamp()
    stale_days = _stale_days_for(qtype)
    return (now - float(epoch_ts)) <= (stale_days * 86400)


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

# Type inference helpers
_ISO_FIAT_OR_CRYPTO = {"USD","USDT","EUR","GBP","JPY","AUD","CAD","CHF","CNY","KRW","BTC","ETH","BNB","SOL"}
_CRYPTO_RE = re.compile(r"^[A-Z0-9]{1,15}-(USD|USDT|EUR|BTC|ETH|BNB|SOL)$")

def _infer_quote_type(sym: str) -> Optional[str]:
    """Infer quote type from symbol pattern for type-aware freshness"""
    s = (sym or "").upper()
    if s.startswith("^"):
        return "INDEX"
    if s.endswith("=X"):
        return "CURRENCY"
    if s.endswith("=F"):
        return "FUTURE"
    # Conservative crypto inference: common pairs only; avoid false positives like BRK-B
    if _CRYPTO_RE.match(s):
        return "CRYPTOCURRENCY"
    return "EQUITY"  # safest default

def _merge_aggs(dst: Dict[str, int], src: Dict[str, int]) -> None:
    """Merge source aggregates into destination"""
    for k, v in (src or {}).items():
        dst[k] = dst.get(k, 0) + int(v or 0)

def _retry_after_seconds(value: str) -> int:
    """Parse Retry-After header (numeric seconds or HTTP-date)"""
    if not value:
        return 0
    try:
        # Try numeric seconds first
        return max(0, int(float(value)))
    except Exception:
        pass
    try:
        # Try HTTP-date format
        dt = parsedate_to_datetime(value)
        if not dt:
            return 0
        # normalize to UTC and diff
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(delta))
    except Exception:
        return 0


# Quote API functions
def _mk_from_quote(sym: str, q: dict) -> Dict:
    """Build result from quote API response"""
    qtype = (q.get("quoteType") or "").upper() or None
    exch = q.get("fullExchangeName") or q.get("exchange") or None
    cur = q.get("currency") or None
    epoch = q.get("regularMarketTime") or q.get("postMarketTime") or q.get("preMarketTime")
    has_price = q.get("regularMarketPrice") is not None
    
    # Decide freshness by type-aware window
    active = False
    if epoch:
        active = _is_active_epoch(epoch, qtype)
    
    if has_price and epoch and active:
        return {
            "symbol": sym,
            "exists": True,
            "active": True,
            "quoteType": qtype,
            "exchange": exch,
            "currency": cur,
            "regularMarketTime": int(epoch),
            "regularMarketTime_iso": _epoch_to_iso(epoch),
            "meta_exists": True,
            "has_price": True,
            "status": "active",
        }
    elif has_price or epoch:
        # We have metadata but either price missing or too old → stale
        return {
            "symbol": sym,
            "exists": True,
            "active": False,
            "quoteType": qtype,
            "exchange": exch,
            "currency": cur,
            "regularMarketTime": int(epoch) if epoch else None,
            "regularMarketTime_iso": _epoch_to_iso(epoch) if epoch else None,
            "meta_exists": True,
            "has_price": bool(has_price),
            "status": "stale",
        }
    else:
        # No useful data
        return _result_invalid(sym, "no_data", "No price or time in quote")


def _validate_with_quote(symbols: List[str], throttler: DynamicThrottler = None) -> Tuple[Dict[str, Dict], Dict[str, int]]:
    """
    Classify via Yahoo quote API. Fast path for invalid/active/stale.
    Returns (results_by_sym, aggregates).
    """
    results: Dict[str, Dict] = {}
    agg = {"rate_limit": 0, "timeout": 0, "no_price_data": 0, "not_found": 0, "other": 0}
    
    if not throttler:
        throttler = DynamicThrottler()
    
    session = _make_http_session()
    batch_size = min(throttler.get_batch_size(), QUOTE_BATCH_SIZE)
    
    for chunk in _chunked(symbols, batch_size):
        try:
            r = session.get(QUOTE_URL, params={"symbols": ",".join(chunk)}, timeout=REQUEST_TIMEOUT)
            
            # Handle 401 Unauthorized - Yahoo has restricted the Quote API
            if r.status_code == 401:
                # Mark all as unknown to trigger fallback
                for s in chunk:
                    results[s] = _unknown_record(s, "other", "quote API unauthorized")
                agg["other"] += len(chunk)
                continue
            
            if r.status_code == 429:
                # Rate limit hit - honor Retry-After if present
                retry_after = _retry_after_seconds(r.headers.get("Retry-After", "0"))
                agg["rate_limit"] += len(chunk)
                for s in chunk:
                    results[s] = _unknown_record(s, "rate_limit", "quote 429")
                throttler.on_rate_limit()
                
                # Respect Yahoo's Retry-After header
                if retry_after > 0:
                    print(f"Yahoo says wait {retry_after}s - respecting Retry-After header")
                    time.sleep(min(retry_after, 300))  # Cap at 5 minutes
                continue
                
            r.raise_for_status()
            payload = r.json() or {}
            data = (payload.get("quoteResponse") or {}).get("result") or []
            
            # Map returned symbols
            returned = {(row.get("symbol") or "").upper(): row for row in data}
            
            # For each requested symbol: if not returned → invalid quickly
            for s in chunk:
                row = returned.get(s)
                if row:
                    results[s] = _mk_from_quote(s, row)
                else:
                    # Not recognized by quote endpoint → invalid
                    results[s] = _result_invalid(s, code="not_found", msg="not in quote response")
                    agg["not_found"] += 1
            
            # Success - update throttler
            throttler.on_success(len(chunk))
            
        except requests.Timeout:
            agg["timeout"] += len(chunk)
            for s in chunk:
                results[s] = _unknown_record(s, "timeout", "quote timeout")
        except Exception as e:
            code = classify_error(e, str(e))
            agg[code] = agg.get(code, 0) + len(chunk)
            for s in chunk:
                results[s] = _unknown_record(s, code, str(e)[:200])
        
        # Apply delay between batches
        time.sleep(throttler.get_delay())
    
    return results, agg


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


def _mk_result(sym: str, last_dt: Optional[pd.Timestamp], qtype_hint: Optional[str] = None) -> Dict:
    """Return ACTIVE or STALE (if last_dt too old) with meta_exists/has_price set."""
    if last_dt is None:
        # No bars => no price data but metadata exists
        return {
            "symbol": sym,
            "exists": True,              # symbol path succeeded, but no bars
            "active": False,
            "quoteType": qtype_hint,
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
    is_active = _is_active_epoch(epoch, qtype_hint)
    return {
        "symbol": sym,
        "exists": True,
        "active": is_active,
        "quoteType": qtype_hint,
        "exchange": None,
        "currency": None,
        "regularMarketTime": epoch,
        "regularMarketTime_iso": last_dt.isoformat(timespec="seconds"),
        "meta_exists": True,
        "has_price": True,
        "status": "active" if is_active else "stale",
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
                threads=threads, prepost=False, rounding=False
                # Note: raise_errors parameter not available in all yfinance versions
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
                res[sym] = _mk_result(sym, last_dt, qtype_hint=_infer_quote_type(sym))
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
                    res[sym] = _mk_result(sym, last_dt, qtype_hint=_infer_quote_type(sym))
            except Exception:
                # Not present -> let fallback handle
                pass
    return res


def _validate_batch_download_with_watchdog(batch: List[str], threads: bool = True, timeout_s: int = _DL_BATCH_TIMEOUT_S) -> Dict[str, Dict]:
    """
    Run yf.download in a 1-worker pool and enforce a hard timeout.
    On timeout, return UNKNOWN/TIMEOUT for the whole batch so the pipeline can continue.
    """
    def _runner():
        return _validate_batch_download(batch, threads=threads)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_runner)
        try:
            return fut.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            # mark all as timeout; caller will increment progress & aggregates
            return {s: _unknown_record(s, "timeout", "yf.download watchdog timeout") for s in batch}


def _fallback_single_history(sym: str) -> Dict:
    delay = RETRY_BACKOFF_BASE
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with redirect_stdout(_SilentIO()), redirect_stderr(_SilentIO()):
                hist = yf.Ticker(sym).history(
                    period="1mo", interval="1d",
                    auto_adjust=False, prepost=False, actions=False
                )

            # If history call itself returns nothing → treat as INVALID.
            # Consistently empty DataFrames indicate the symbol doesn't exist
            if not isinstance(hist, pd.DataFrame) or hist.empty:
                return _result_invalid(sym, code="not_found", msg="no history")

            # Have a frame; classify based on data quality.
            if "Close" in hist.columns:
                s = hist["Close"].dropna()
                if not s.empty:
                    last_dt = s.index[-1]
                    return _mk_result(sym, last_dt, qtype_hint=_infer_quote_type(sym))
                # We did get a frame but no valid close values
                return {
                    "symbol": sym,
                    "exists": True, "meta_exists": True, "has_price": False,
                    "active": False, "status": "stale",
                    "error_code": "no_price_data", "error_msg": "",
                    "quoteType": None, "exchange": None, "currency": None,
                    "regularMarketTime": None, "regularMarketTime_iso": None,
                }

            # Frame without Close column → unknown (data quality issue)
            return _unknown_record(sym, code="no_price_data", msg="no Close in history")

        except Exception as e:
            code = classify_error(e, str(e))
            if attempt == MAX_RETRIES:
                # If we consistently get "not_found" errors, mark as invalid
                if code == "not_found" or "symbol may be delisted" in str(e).lower():
                    return _result_invalid(sym, code, str(e))
                return _unknown_record(sym, code, str(e))
            time.sleep(min(delay, RETRY_BACKOFF_MAX))
            delay *= 2

    return _unknown_record(sym, "other", "max retries")


def validate_symbols(symbols: List[str], gentle: bool = False, progress: bool = False, 
                    timeout: int = 10, session: Optional[object] = None) -> Tuple[List[Dict], Dict[str, int]]:
    """Quote-first hybrid validator with structured error reporting"""
    # Progress writer (if available) - only use if progress=True
    write_progress = None
    if progress:
        try:
            from global_ticker_library.registry import write_progress
        except ImportError:
            write_progress = None
    
    # Use symbols AS-IS (except trimming + uppercase); NO reshaping at all.
    # Keep original for reporting; use UPPER for DB consistency.
    symbol_map: Dict[str, str] = {}  # normalized -> original
    ordered: List[str] = []
    seen = set()
    
    for raw in symbols:
        if not raw or not raw.strip():
            continue
        orig = raw.strip()  # preserve exact user input
        norm = orig.upper()  # uppercase for consistency
        if norm not in seen:
            seen.add(norm)
            ordered.append(norm)
            symbol_map[norm] = orig
    
    aggregates: Dict[str, int] = {"rate_limit": 0, "timeout": 0, "no_price_data": 0, "not_found": 0, "other": 0}
    all_results: Dict[str, Dict] = {}
    
    print(f"Starting validation for {len(ordered)} symbols...")
    
    fallback_targets: List[str] = ordered  # default, in case we skip quote
    
    if ENABLE_QUOTE_PHASE:
        # 2) QUOTE FIRST (fast classification) - but may fail if Yahoo restricts access
        throttler = DynamicThrottler(initial_batch=50 if gentle else 120,
                                     initial_delay=0.5 if gentle else 0.2)
        
        if write_progress:
            write_progress({
                "status": "running",
                "phase": "quote",
                "total": len(ordered),
                "done": 0,
                "message": f"Quote phase starting... 0/{len(ordered)}"
            })
        
        quote_results, quote_agg = _validate_with_quote(ordered, throttler=throttler)
        _merge_aggs(aggregates, quote_agg)
        all_results.update(quote_results)
        
        # compute fallback targets from quote output
        fallback_targets = []
        for s in ordered:
            r = all_results.get(s)
            if not r or r.get("status") == "unknown" or (r.get("status") == "stale" and not r.get("has_price")):
                fallback_targets.append(s)
        
        if write_progress:
            write_progress({
                "status": "running",
                "phase": "quote",
                "total": len(ordered),
                "done": len(ordered),
                "message": f"Quote phase complete. {len(fallback_targets)} need fallback",
                "rate_limits": aggregates.get("rate_limit", 0),
                "timeouts": aggregates.get("timeout", 0),
                "no_price_data": aggregates.get("no_price_data", 0),
                "other_errors": aggregates.get("other", 0)
            })
        
        print(f"Quote phase complete. {len(fallback_targets)} symbols need fallback validation.")
    else:
        print("Quote phase disabled (YF_ENABLE_QUOTE_PHASE=0). Using yfinance-only validation.")
    
    # 3) DOWNLOAD/HISTORY ONLY FOR TARGETS
    if fallback_targets:
        # yfinance batch download first
        dl_batch = min(_DL_BATCH, 50 if gentle else _DL_BATCH)
        dl_batches = list(_chunked(fallback_targets, dl_batch))
        processed = 0
        
        for i, batch in enumerate(dl_batches):
            try:
                partial = _validate_batch_download_with_watchdog(batch, threads=not gentle, timeout_s=_DL_BATCH_TIMEOUT_S)
                # Merge partial and update aggregates from statuses
                for sym, rec in partial.items():
                    all_results[sym] = rec
                    st = rec.get("status")
                    if st == "unknown" and rec.get("error_code") == "timeout":
                        aggregates["timeout"] = aggregates.get("timeout", 0) + 1
                    elif st == "stale" and not rec.get("has_price", False):
                        aggregates["no_price_data"] = aggregates.get("no_price_data", 0) + 1
            except Exception as e:
                code = classify_error(e, str(e))
                aggregates[code] = aggregates.get(code, 0) + len(batch)
                for sym in batch:
                    prior = all_results.get(sym)
                    if not prior or prior.get("status") == "unknown":
                        all_results[sym] = _unknown_record(sym, code, str(e))
            
            processed += len(batch)
            
            # Progress on EVERY batch (not just every 5th)
            if write_progress:
                write_progress({
                    "status": "running",
                    "phase": "download",
                    "total": len(fallback_targets),
                    "done": processed,
                    "message": f"Download fallback... {processed}/{len(fallback_targets)}",
                    "rate_limits": aggregates.get("rate_limit", 0),
                    "timeouts": aggregates.get("timeout", 0),
                    "no_price_data": aggregates.get("no_price_data", 0),
                    "other_errors": aggregates.get("other", 0),
                    "batch_number": i + 1,
                    "total_batches": len(dl_batches),
                })
            
            time.sleep(0.2 if gentle else INTER_BATCH_SLEEP)
        
        # Fallback per-symbol history for the remaining misses
        misses = [s for s in fallback_targets if s not in all_results or all_results[s].get("status") == "unknown"]
        if misses:
            workers = 4 if gentle else _FALLBACK_WORKERS
            # Derive a pool cap: scale with count, clip in [HIST_POOL_MIN_S, HIST_POOL_MAX_S]
            per_sym = max(3, min(12, timeout))  # seconds/symbol (bounded)
            pool_cap = max(_HIST_POOL_MIN_S, min(_HIST_POOL_MAX_S, per_sym * len(misses) // workers))
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                fut2sym = {ex.submit(_fallback_single_history, sym): sym for sym in misses}
                done, not_done = concurrent.futures.wait(fut2sym.keys(), timeout=pool_cap)
                
                # consume finished futures
                for fut in done:
                    sym = fut2sym[fut]
                    try:
                        out = fut.result()
                        code = out.get("error_code")
                        if code:
                            aggregates[code] = aggregates.get(code, 0) + 1
                        all_results[sym] = out
                    except Exception as e:
                        code = classify_error(e, str(e))
                        aggregates[code] = aggregates.get(code, 0) + 1
                        all_results[sym] = _unknown_record(sym, code, str(e)[:200])
                
                # cancel and mark the stragglers
                for fut in not_done:
                    sym = fut2sym[fut]
                    fut.cancel()
                    aggregates["timeout"] = aggregates.get("timeout", 0) + 1
                    all_results[sym] = _unknown_record(sym, "timeout", "history pool timeout")
    
    # 4) Build final result list in original order with original symbol attached
    final_results: List[Dict] = []
    for s in ordered:
        r = all_results.get(s) or _unknown_record(s, "other", "no result")
        r["symbol"] = s  # normalized UPPER
        r["original"] = symbol_map.get(s, s)  # human-visible original as entered
        
        if r.get("status") not in ("active", "stale", "invalid", "unknown"):
            r = {**r, **_unknown_record(s, r.get("error_code", "other"), r.get("error_msg", ""))}
        final_results.append(r)
    
    return final_results, aggregates