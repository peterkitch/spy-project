"""
SQLite Registry Management
Single source of truth for ticker status and metadata
"""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Set

import random
from global_ticker_library.gl_config import (
    DB_PATH, MASTER_FILE, REMOVAL_CONFIRMATIONS, STALE_DAYS,
    CACHE_TTL_HOURS, REMOVALS_LOG_FILE, PROGRESS_FILE,
    UNKNOWN_RETRY_MINUTES, STALE_RECHECK_DAYS, INVALID_RECHECK_DAYS
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickers (
  symbol TEXT PRIMARY KEY COLLATE NOCASE,
  quote_type TEXT,
  exchange TEXT,
  currency TEXT,
  status TEXT CHECK(status IN ('candidate','active','stale','invalid','unknown')) DEFAULT 'candidate',
  first_seen_utc TEXT,
  last_verified_utc TEXT,
  last_market_time_utc TEXT,
  stale_strikes INTEGER DEFAULT 0,
  source_json TEXT,
  retry_count INTEGER DEFAULT 0,
  last_error_code TEXT,
  last_error_msg TEXT,
  canonical TEXT,
  original TEXT
);
CREATE INDEX IF NOT EXISTS idx_status ON tickers(status);
CREATE INDEX IF NOT EXISTS idx_last_verified ON tickers(last_verified_utc);
CREATE INDEX IF NOT EXISTS idx_last_market ON tickers(last_market_time_utc);
"""

def _now_iso() -> str:
    """Get current UTC timestamp in ISO format"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _parse_iso(iso_str: str) -> datetime:
    """Parse ISO timestamp string to datetime"""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
    except:
        return None

def init_db(db_path: Path = DB_PATH) -> None:
    """Initialize database, add columns, and migrate schema if CHECK constraint lacks 'unknown'."""
    with sqlite3.connect(db_path) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA cache_size=10000")
        con.execute("PRAGMA busy_timeout=3000")

        cur = con.cursor()
        # Ensure table exists, or detect old table for migration
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tickers'")
        exists = cur.fetchone() is not None

        if not exists:
            con.executescript(SCHEMA)
        else:
            # Check whether CHECK constraint already includes 'unknown'
            cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tickers'")
            create_sql = (cur.fetchone() or [""])[0] or ""
            if "CHECK(status IN ('candidate','active','stale','invalid','unknown'))" not in create_sql:
                # Migrate: rename -> recreate -> copy intersection of columns -> drop old
                cur.execute("ALTER TABLE tickers RENAME TO tickers_old")
                con.executescript(SCHEMA)
                old_cols = [r[1] for r in con.execute("PRAGMA table_info(tickers_old)")]
                new_cols = [r[1] for r in con.execute("PRAGMA table_info(tickers)")]
                cols = [c for c in new_cols if c in old_cols]  # copy what we can
                col_list = ",".join(cols)
                con.execute(f"INSERT INTO tickers ({col_list}) SELECT {col_list} FROM tickers_old")
                cur.execute("DROP TABLE tickers_old")

        # Add any missing columns (idempotent)
        cur.execute("PRAGMA table_info(tickers)")
        columns = {row[1] for row in cur.fetchall()}
        if 'retry_count' not in columns:
            con.execute("ALTER TABLE tickers ADD COLUMN retry_count INTEGER DEFAULT 0")
        if 'last_error_code' not in columns:
            con.execute("ALTER TABLE tickers ADD COLUMN last_error_code TEXT")
        if 'last_error_msg' not in columns:
            con.execute("ALTER TABLE tickers ADD COLUMN last_error_msg TEXT")
        if 'canonical' not in columns:
            con.execute("ALTER TABLE tickers ADD COLUMN canonical TEXT")
        if 'original' not in columns:
            con.execute("ALTER TABLE tickers ADD COLUMN original TEXT")
        con.commit()

def upsert_candidates(symbols: Iterable[str], source_tag: str, db_path: Path = DB_PATH) -> int:
    """Insert symbols as candidates if missing; keep existing status unchanged"""
    # Import canonicalize locally to avoid circular import
    from global_ticker_library.validator_yahoo import canonicalize
    
    payload = _source_blob(source_tag)
    now = _now_iso()
    inserted = 0
    
    with sqlite3.connect(db_path) as con, closing(con.cursor()) as cur:
        for s in symbols:
            orig = (s or "").strip().upper()
            if not orig:
                continue
            
            # Canonicalize the symbol
            s = canonicalize(orig)
            if not s:
                continue
            
            try:
                # Store both original and canonical forms
                cur.execute(
                    """INSERT OR IGNORE INTO tickers(symbol, first_seen_utc, source_json, original, canonical)
                       VALUES(?,?,?,?,?)""",
                    (s, now, payload, orig, s),
                )
                if cur.rowcount:
                    inserted += 1
                    
                # If row already exists, opportunistically update original/canonical
                cur.execute(
                    "UPDATE tickers SET original=COALESCE(original, ?), canonical=COALESCE(canonical, ?) WHERE symbol=?",
                    (orig, s, s)
                )
            except sqlite3.IntegrityError:
                pass
        con.commit()
    return inserted

def _source_blob(tag: str) -> str:
    """Create source metadata JSON"""
    return json.dumps({"source": tag, "ingested_utc": _now_iso()})

def _backoff_seconds(retry_count: int) -> int:
    # 15min, 30min, 60min, ... cap at 12h
    base = 15 * 60
    return min(base * (2 ** max(0, retry_count - 1)), 12 * 60 * 60)

def get_symbols_to_validate(
    db_path: Path = DB_PATH,
    ttl_hours: int = CACHE_TTL_HOURS,
    force: bool = False,
    only_status: Optional[Set[str]] = None,
) -> List[str]:
    """
    Return symbols to validate.
    - If force=True: all non-active + active older than TTL.
    - If force=False: non-active older than TTL (or never verified) + active older than TTL.
    - If only_status is provided, filter non-active to that subset (e.g. {"candidate","unknown","stale"}).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    cutoff_iso = cutoff.isoformat(timespec="seconds")

    non_active_statuses = {"candidate", "unknown", "stale", "invalid"}
    if only_status:
        non_active_statuses &= set(only_status)

    with sqlite3.connect(db_path) as con, closing(con.cursor()) as cur:
        symbols = []

        if force:
            # All non-active (no TTL filter)
            if non_active_statuses:
                q_marks = ",".join("?" for _ in non_active_statuses)
                cur.execute(
                    f"SELECT symbol FROM tickers WHERE status IN ({q_marks})",
                    tuple(non_active_statuses),
                )
                symbols.extend(r[0] for r in cur.fetchall())
        else:
            # Non-active older than TTL (or never verified)
            if non_active_statuses:
                q_marks = ",".join("?" for _ in non_active_statuses)
                cur.execute(
                    f"""SELECT symbol FROM tickers 
                        WHERE status IN ({q_marks})
                          AND (last_verified_utc IS NULL OR last_verified_utc < ?)""",
                    (*non_active_statuses, cutoff_iso),
                )
                symbols.extend(r[0] for r in cur.fetchall())

        # Active older than TTL are always eligible (both force and non-force)
        cur.execute(
            "SELECT symbol FROM tickers WHERE status = 'active' AND "
            "(last_verified_utc IS NULL OR last_verified_utc < ?)",
            (cutoff_iso,),
        )
        symbols.extend(r[0] for r in cur.fetchall())

    # unique while preserving order
    seen, out = set(), []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out

def to_validate_breakdown(
    db_path: Path = DB_PATH,
    ttl_hours: int = CACHE_TTL_HOURS,
    force: bool = False,
) -> Dict[str, int]:
    """
    Helper to print how many per status WOULD be validated under current policy.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    cutoff_iso = cutoff.isoformat(timespec="seconds")

    rows = {
        "candidate": 0,
        "unknown":   0,
        "stale":     0,
        "invalid":   0,
        "active_ttl":0,
    }

    with sqlite3.connect(db_path) as con, closing(con.cursor()) as cur:
        for st in ("candidate","unknown","stale","invalid"):
            if force:
                cur.execute("SELECT COUNT(*) FROM tickers WHERE status=?", (st,))
            else:
                cur.execute(
                    "SELECT COUNT(*) FROM tickers WHERE status=? AND (last_verified_utc IS NULL OR last_verified_utc < ?)",
                    (st, cutoff_iso),
                )
            rows[st] = int(cur.fetchone()[0] or 0)

        cur.execute(
            "SELECT COUNT(*) FROM tickers WHERE status='active' AND (last_verified_utc IS NULL OR last_verified_utc < ?)",
            (cutoff_iso,),
        )
        rows["active_ttl"] = int(cur.fetchone()[0] or 0)

    return rows

def upsert_validation_results(
    records: List[Dict], db_path: Path = DB_PATH
) -> Tuple[int, int, int, int, List[str], List[str]]:
    """
    Update ticker status based on structured validation results
    Returns: (n_active, n_stale, n_invalid, n_unknown, additions, removals)
    """
    now = _now_iso()
    n_active = n_stale = n_invalid = n_unknown = 0
    additions = []
    removals = []
    
    with sqlite3.connect(db_path) as con, closing(con.cursor()) as cur:
        for r in records:
            # Extract fields from structured result
            original = r.get("original", r["symbol"]).upper()
            sym = r.get("symbol", original).upper()  # canonical form
            status = r.get("status", "unknown")
            error_code = r.get("error_code")
            error_msg = r.get("error_msg", "")[:200]  # Limit message length
            meta_exists = r.get("meta_exists", False)
            has_price = r.get("has_price", False)
            
            # Additional metadata
            qtype = r.get("quoteType")
            exch = r.get("exchange")
            curr = r.get("currency")
            lmt = r.get("regularMarketTime_iso")
            
            # Get current status and retry count - lookup by ORIGINAL symbol
            cur.execute("SELECT status, retry_count FROM tickers WHERE symbol=?", (original,))
            row = cur.fetchone()
            old_status = row[0] if row else None
            retry_count = row[1] if row else 0
            
            # Ensure row exists - use ORIGINAL as the primary key
            cur.execute(
                """INSERT OR IGNORE INTO tickers(symbol, first_seen_utc, original, canonical) 
                   VALUES(?,?,?,?)""",
                (original, now, original, sym),
            )
            
            # Apply status rules based on error classification
            is_active = bool(r.get("active"))
            
            if status == "unknown" or error_code in ("rate_limit", "timeout"):
                # Transient error - mark as unknown for retry
                retry_count += 1
                cur.execute(
                    """UPDATE tickers
                       SET status='unknown', retry_count=?, last_error_code=?, 
                           last_error_msg=?, last_verified_utc=?
                       WHERE symbol=?""",
                    (retry_count, error_code, error_msg, now, original)
                )
                n_unknown += 1
                
            elif status == "active" or (meta_exists and has_price and is_active):
                # ACTIVE only when either validator says so, or both flags are true AND active=True
                cur.execute(
                    """UPDATE tickers
                       SET status='active', quote_type=?, exchange=?, currency=?,
                           last_verified_utc=?, last_market_time_utc=?, stale_strikes=0,
                           retry_count=0, last_error_code=NULL, last_error_msg=NULL, canonical=?
                       WHERE symbol=?""",
                    (qtype, exch, curr, now, lmt, sym, original)
                )
                n_active += 1
                if old_status != 'active':
                    additions.append(sym)
                    
            elif status == "stale" or (meta_exists and not has_price) or (meta_exists and has_price and not is_active):
                # stale if meta exists but no price or price is too old
                cur.execute("SELECT COALESCE(stale_strikes,0) FROM tickers WHERE symbol=?", (original,))
                row = cur.fetchone()
                strikes = (row[0] if row else 0) + 1
                
                cur.execute(
                    """UPDATE tickers
                       SET status='stale', quote_type=?, exchange=?, currency=?,
                           last_verified_utc=?, last_market_time_utc=?, stale_strikes=?,
                           retry_count=0, last_error_code=?, last_error_msg=?, canonical=?
                       WHERE symbol=?""",
                    (qtype, exch, curr, now, lmt, min(strikes, REMOVAL_CONFIRMATIONS + 1),
                     error_code, error_msg, sym, original)
                )
                n_stale += 1
                if old_status == 'active':
                    removals.append(sym)
                    
            elif not meta_exists and retry_count >= 2:
                # No metadata after retries - mark as invalid
                cur.execute(
                    """UPDATE tickers
                       SET status='invalid', last_verified_utc=?, 
                           last_error_code=?, last_error_msg=?
                       WHERE symbol=?""",
                    (now, error_code or "not_found", error_msg, original)
                )
                n_invalid += 1
                if old_status == 'active':
                    removals.append(sym)
                    
            else:
                # Ambiguous - mark as unknown for retry
                retry_count += 1
                cur.execute(
                    """UPDATE tickers
                       SET status='unknown', retry_count=?, last_error_code=?, 
                           last_error_msg=?, last_verified_utc=?
                       WHERE symbol=?""",
                    (retry_count, error_code, error_msg, now, original)
                )
                n_unknown += 1
        
        con.commit()
    
    return n_active, n_stale, n_invalid, n_unknown, additions, removals

def cleanup_stale(db_path: Path = DB_PATH, dry_run: bool = False) -> Tuple[int, List[str]]:
    """
    Invalidate entries with sufficient stale strikes.
    Returns: (number affected, list of (to-be) invalidated symbols)
    If dry_run=True, no DB changes are committed.
    """
    invalidated: List[str] = []
    affected = 0

    with sqlite3.connect(db_path) as con, closing(con.cursor()) as cur:
        # Compute candidates
        cur.execute(
            "SELECT symbol FROM tickers WHERE status='stale' AND stale_strikes >= ?",
            (REMOVAL_CONFIRMATIONS,),
        )
        invalidated = [r[0] for r in cur.fetchall()]

        if not dry_run and invalidated:
            cur.execute(
                "UPDATE tickers SET status='invalid' "
                "WHERE status='stale' AND stale_strikes >= ?",
                (REMOVAL_CONFIRMATIONS,),
            )
            affected = cur.rowcount or 0
            con.commit()
        else:
            affected = len(invalidated)

    return affected, invalidated

def export_active(master_path: Path = MASTER_FILE, db_path: Path = DB_PATH) -> int:
    """Export active symbols to master_tickers.txt"""
    with sqlite3.connect(db_path) as con, closing(con.cursor()) as cur:
        cur.execute(
            "SELECT symbol FROM tickers WHERE status='active' "
            "ORDER BY symbol COLLATE NOCASE"
        )
        symbols = [r[0] for r in cur.fetchall()]
    
    master_path.write_text(",".join(symbols), encoding="utf-8")
    return len(symbols)

def get_active_by_source(source_pattern: str, db_path: Path = DB_PATH) -> List[str]:
    """Get active symbols that came from a specific source pattern"""
    with sqlite3.connect(db_path) as con, closing(con.cursor()) as cur:
        cur.execute(
            """SELECT symbol FROM tickers 
               WHERE status='active' 
               AND source_json LIKE ?
               ORDER BY symbol COLLATE NOCASE""",
            (f'%"source": "{source_pattern}"%',)
        )
        return [r[0] for r in cur.fetchall()]

# Function removed - no longer tracking scraped vs manual separately

def write_removal_log(removals: List[str]) -> None:
    """Log removed symbols with timestamp."""
    if not removals or not REMOVALS_LOG_FILE:
        return

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    with open(REMOVALS_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] removals ({len(removals)}):\n")
        for s in sorted(removals):
            f.write(f"{s}\n")

def counts(db_path: Path = DB_PATH) -> Dict[str, int]:
    """Get counts by status"""
    out = {"candidate": 0, "active": 0, "stale": 0, "invalid": 0, "unknown": 0, "total": 0}
    
    with sqlite3.connect(db_path) as con, closing(con.cursor()) as cur:
        for k in ("candidate", "active", "stale", "invalid", "unknown"):
            cur.execute("SELECT COUNT(*) FROM tickers WHERE status=?", (k,))
            out[k] = int(cur.fetchone()[0])
    
    out["total"] = sum(out[k] for k in ("candidate", "active", "stale", "invalid", "unknown"))
    return out

def get_recent_changes(limit: int = 100, db_path: Path = DB_PATH) -> Dict[str, List[str]]:
    """Get recently added and removed symbols"""
    out = {"additions": [], "removals": []}
    
    with sqlite3.connect(db_path) as con, closing(con.cursor()) as cur:
        # Recent additions (became active)
        cur.execute(
            """SELECT symbol FROM tickers 
               WHERE status='active' 
               ORDER BY last_verified_utc DESC 
               LIMIT ?""",
            (limit,)
        )
        out["additions"] = [r[0] for r in cur.fetchall()]
        
        # Recent removals (became invalid)
        cur.execute(
            """SELECT symbol FROM tickers 
               WHERE status='invalid' 
               ORDER BY last_verified_utc DESC 
               LIMIT ?""",
            (limit,)
        )
        out["removals"] = [r[0] for r in cur.fetchall()]
    
    return out

def backoff_for(retry_count: int) -> int:
    """Calculate exponential backoff with jitter for retries
    Returns seconds: 2, 4, 8, 16, cap at 60 + jitter"""
    base = min(2 ** max(1, retry_count), 60)
    jitter = random.randint(0, 2)
    return base + jitter

def get_symbols_by_status(status: str, limit: int = 100000, db_path: Path = DB_PATH) -> List[str]:
    """Get symbols with a specific status"""
    with sqlite3.connect(db_path) as con, closing(con.cursor()) as cur:
        cur.execute(
            "SELECT symbol FROM tickers WHERE status=? LIMIT ?",
            (status, limit)
        )
        return [r[0] for r in cur.fetchall()]

def write_progress(payload: Dict) -> None:
    """Write progress update to file for Dashboard to read"""
    try:
        import json
        # Add timestamp
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        
        # Write to temp file first for atomicity
        tmp = PROGRESS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PROGRESS_FILE)  # Atomic on most OS
    except Exception:
        # Silently fail - progress tracking is optional
        pass

def clear_progress() -> None:
    """Clear progress file when done"""
    try:
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
    except Exception:
        # Silently fail - progress tracking is optional
        pass