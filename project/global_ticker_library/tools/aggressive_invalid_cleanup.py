#!/usr/bin/env python3
"""
Aggressive cleanup of invalid symbols.
Marks symbols as invalid that consistently fail validation.
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from global_ticker_library.gl_config import DB_PATH
from global_ticker_library.registry import counts, export_active

def cleanup_invalid_symbols(dry_run=False):
    """Mark consistently failing symbols as invalid."""
    
    print("="*60)
    print("AGGRESSIVE INVALID SYMBOL CLEANUP")
    print("="*60)
    
    # Get initial counts
    before = counts()
    print(f"\nBefore cleanup:")
    print(f"  Unknown: {before.get('unknown', 0):,}")
    print(f"  Invalid: {before.get('invalid', 0):,}")
    print(f"  Candidates: {before.get('candidate', 0):,}")
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        now = datetime.now(timezone.utc).isoformat()
        
        # 1. Mark unknown symbols with 2+ retries as invalid
        print("\n" + "="*60)
        print("1. UNKNOWN SYMBOLS WITH MULTIPLE RETRIES")
        print("="*60)
        
        cur.execute("""
            SELECT COUNT(*) 
            FROM tickers 
            WHERE status = 'unknown' 
            AND retry_count >= 2
            AND last_error_code IN ('not_found', 'no_price_data')
        """)
        unknown_to_invalid = cur.fetchone()[0]
        print(f"Found {unknown_to_invalid:,} unknown symbols with 2+ retries and not_found errors")
        
        if not dry_run and unknown_to_invalid > 0:
            cur.execute("""
                UPDATE tickers
                SET status = 'invalid',
                    last_error_msg = COALESCE(last_error_msg, 'Multiple validation failures'),
                    invalidated_utc = ?
                WHERE status = 'unknown' 
                AND retry_count >= 2
                AND last_error_code IN ('not_found', 'no_price_data')
            """, (now,))
            print(f"Marked {cur.rowcount:,} symbols as invalid")
        
        # 2. Mark stuck candidates as invalid
        print("\n" + "="*60)
        print("2. STUCK CANDIDATES")
        print("="*60)
        
        cur.execute("""
            SELECT COUNT(*) 
            FROM tickers 
            WHERE status = 'candidate' 
            AND last_verified_utc IS NOT NULL
        """)
        stuck_candidates = cur.fetchone()[0]
        print(f"Found {stuck_candidates:,} stuck candidates (verified but still candidates)")
        
        # Sample check to see what they look like
        cur.execute("""
            SELECT symbol 
            FROM tickers 
            WHERE status = 'candidate' 
            AND last_verified_utc IS NOT NULL
            LIMIT 10
        """)
        samples = [row[0] for row in cur.fetchall()]
        print(f"Samples: {samples}")
        
        if not dry_run and stuck_candidates > 0:
            cur.execute("""
                UPDATE tickers
                SET status = 'invalid',
                    last_error_code = 'validation_failed',
                    last_error_msg = 'Stuck candidate - no valid data',
                    invalidated_utc = ?
                WHERE status = 'candidate' 
                AND last_verified_utc IS NOT NULL
            """, (now,))
            print(f"Marked {cur.rowcount:,} stuck candidates as invalid")
        
        # 3. Clean up numeric/garbage symbols
        print("\n" + "="*60)
        print("3. NUMERIC AND GARBAGE SYMBOLS")
        print("="*60)
        
        # Negative numbers and pure numeric symbols (but exclude crypto pairs)
        cur.execute("""
            SELECT COUNT(*) 
            FROM tickers 
            WHERE status IN ('unknown', 'candidate')
            AND (symbol GLOB '-[0-9]*' OR symbol GLOB '[0-9]*')
            AND symbol NOT LIKE '%-USD'
            AND symbol NOT LIKE '%-USDT'
            AND symbol NOT LIKE '%-BTC'
            AND symbol NOT LIKE '%-ETH'
            AND symbol NOT LIKE '%-EUR'
            AND symbol NOT LIKE '%-GBP'
            AND symbol NOT LIKE '%-USDC'
            AND symbol NOT LIKE '%-BUSD'
            AND symbol NOT LIKE '%.%'  -- Exclude international stocks like 2330.TW
        """)
        numeric_symbols = cur.fetchone()[0]
        print(f"Found {numeric_symbols:,} numeric symbols (excluding crypto pairs)")
        
        if not dry_run and numeric_symbols > 0:
            cur.execute("""
                UPDATE tickers
                SET status = 'invalid',
                    last_error_code = 'invalid_format',
                    last_error_msg = 'Invalid symbol format (numeric)',
                    invalidated_utc = ?
                WHERE status IN ('unknown', 'candidate')
                AND (symbol GLOB '-[0-9]*' OR symbol GLOB '[0-9]*')
                AND symbol NOT LIKE '%-USD'
                AND symbol NOT LIKE '%-USDT'
                AND symbol NOT LIKE '%-BTC'
                AND symbol NOT LIKE '%-ETH'
                AND symbol NOT LIKE '%-EUR'
                AND symbol NOT LIKE '%-GBP'
                AND symbol NOT LIKE '%-USDC'
                AND symbol NOT LIKE '%-BUSD'
                AND symbol NOT LIKE '%.%'
            """, (now,))
            print(f"Marked {cur.rowcount:,} numeric symbols as invalid")
        
        # Common garbage text patterns
        garbage_patterns = [
            'FITCH', 'FLEXIBLE', 'INVESTING', 'SCUDDER', 'RYMAN', 
            'PLANS', 'TOWER', 'SHAKE', 'ROYAL', 'FORGE'
        ]
        
        placeholders = ','.join('?' for _ in garbage_patterns)
        cur.execute(f"""
            SELECT COUNT(*) 
            FROM tickers 
            WHERE status IN ('unknown', 'candidate')
            AND symbol IN ({placeholders})
        """, garbage_patterns)
        garbage_count = cur.fetchone()[0]
        
        if garbage_count > 0:
            print(f"Found {garbage_count} garbage text symbols")
            if not dry_run:
                cur.execute(f"""
                    UPDATE tickers
                    SET status = 'invalid',
                        last_error_code = 'invalid_symbol',
                        last_error_msg = 'Not a valid ticker symbol',
                        invalidated_utc = ?
                    WHERE status IN ('unknown', 'candidate')
                    AND symbol IN ({placeholders})
                """, [now] + garbage_patterns)
                print(f"Marked {cur.rowcount} garbage symbols as invalid")
        
        # 4. Mark expired futures as invalid
        print("\n" + "="*60)
        print("4. EXPIRED FUTURES")
        print("="*60)
        
        # Futures older than 6 months that are unknown
        cur.execute("""
            SELECT COUNT(*) 
            FROM tickers 
            WHERE status = 'unknown'
            AND symbol LIKE '%-F'
            AND retry_count >= 2
        """)
        expired_futures = cur.fetchone()[0]
        print(f"Found {expired_futures:,} expired futures contracts")
        
        if not dry_run and expired_futures > 0:
            cur.execute("""
                UPDATE tickers
                SET status = 'invalid',
                    last_error_code = 'expired',
                    last_error_msg = 'Expired futures contract',
                    invalidated_utc = ?
                WHERE status = 'unknown'
                AND symbol LIKE '%-F'
                AND retry_count >= 2
            """, (now,))
            print(f"Marked {cur.rowcount:,} expired futures as invalid")
        
        # 5. Clean up unknown symbols with rate limit/timeout that have been retried
        print("\n" + "="*60)
        print("5. PERSISTENT RATE LIMIT/TIMEOUT FAILURES")
        print("="*60)
        
        cur.execute("""
            SELECT COUNT(*) 
            FROM tickers 
            WHERE status = 'unknown'
            AND retry_count >= 2
            AND last_error_code IN ('rate_limit', 'timeout')
        """)
        persistent_errors = cur.fetchone()[0]
        print(f"Found {persistent_errors:,} symbols with persistent rate limit/timeout errors")
        
        # These should stay as unknown for retry, so just report them
        print("(Keeping these as unknown for future retry)")
        
        if not dry_run:
            con.commit()
    
    # Get final counts
    after = counts()
    
    print("\n" + "="*60)
    print("CLEANUP COMPLETE")
    print("="*60)
    print(f"\nAfter cleanup:")
    print(f"  Unknown: {before.get('unknown', 0):,} -> {after.get('unknown', 0):,} ({after.get('unknown', 0) - before.get('unknown', 0):+,})")
    print(f"  Invalid: {before.get('invalid', 0):,} -> {after.get('invalid', 0):,} ({after.get('invalid', 0) - before.get('invalid', 0):+,})")
    print(f"  Candidates: {before.get('candidate', 0):,} -> {after.get('candidate', 0):,} ({after.get('candidate', 0) - before.get('candidate', 0):+,})")
    
    if not dry_run:
        # Export updated master file
        n = export_active()
        print(f"\nExported {n:,} active symbols to master file")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Aggressive cleanup of invalid symbols")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying database")
    args = parser.parse_args()
    
    cleanup_invalid_symbols(dry_run=args.dry_run)
    print("\nDone!")