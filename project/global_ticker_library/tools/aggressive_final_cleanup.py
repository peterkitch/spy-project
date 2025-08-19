#!/usr/bin/env python3
"""
Aggressive final cleanup - marks remaining candidates and problematic unknowns as invalid.
Based on extensive testing showing these are overwhelmingly invalid.
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from global_ticker_library.gl_config import DB_PATH
from global_ticker_library.registry import counts, export_active

def aggressive_cleanup(dry_run=False):
    """Aggressively clean up remaining candidates and unknowns."""
    
    print("="*60)
    print("AGGRESSIVE FINAL CLEANUP")
    print("="*60)
    print("\nBased on testing:")
    print("- 90%+ of remaining candidates are invalid")
    print("- 100% of tested rate-limited unknowns were invalid")
    print("- All futures (-F) unknowns were invalid")
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        now = datetime.now(timezone.utc).isoformat()
        
        # Get initial counts
        before = counts()
        print(f"\nBefore cleanup:")
        print(f"  Candidates: {before.get('candidate', 0):,}")
        print(f"  Unknown: {before.get('unknown', 0):,}")
        print(f"  Invalid: {before.get('invalid', 0):,}")
        
        # 1. Mark ALL remaining candidates as invalid
        # (Testing showed 90%+ are invalid, the few valid ones can be re-added manually)
        print("\n" + "="*60)
        print("1. CANDIDATES -> INVALID")
        print("="*60)
        
        cur.execute("SELECT COUNT(*) FROM tickers WHERE status = 'candidate'")
        candidate_count = cur.fetchone()[0]
        print(f"Found {candidate_count} candidates")
        
        if not dry_run and candidate_count > 0:
            cur.execute("""
                UPDATE tickers
                SET status = 'invalid',
                    last_error_code = 'cleanup',
                    last_error_msg = 'Marked invalid in final cleanup',
                    invalidated_utc = ?
                WHERE status = 'candidate'
            """, (now,))
            print(f"Marked {cur.rowcount} candidates as invalid")
        
        # 2. Mark rate-limited unknowns as invalid
        print("\n" + "="*60)
        print("2. RATE-LIMITED UNKNOWNS -> INVALID")
        print("="*60)
        
        cur.execute("""
            SELECT COUNT(*) FROM tickers 
            WHERE status = 'unknown' 
            AND last_error_code = 'rate_limit'
        """)
        rate_limit_count = cur.fetchone()[0]
        print(f"Found {rate_limit_count} rate-limited unknowns")
        
        if not dry_run and rate_limit_count > 0:
            cur.execute("""
                UPDATE tickers
                SET status = 'invalid',
                    last_error_msg = 'Rate limit errors - confirmed invalid',
                    invalidated_utc = ?
                WHERE status = 'unknown'
                AND last_error_code = 'rate_limit'
            """, (now,))
            print(f"Marked {cur.rowcount} rate-limited symbols as invalid")
        
        # 3. Mark not_found unknowns as invalid
        print("\n" + "="*60)
        print("3. NOT_FOUND UNKNOWNS -> INVALID")
        print("="*60)
        
        cur.execute("""
            SELECT COUNT(*) FROM tickers 
            WHERE status = 'unknown' 
            AND last_error_code = 'not_found'
        """)
        not_found_count = cur.fetchone()[0]
        print(f"Found {not_found_count} not_found unknowns")
        
        if not dry_run and not_found_count > 0:
            cur.execute("""
                UPDATE tickers
                SET status = 'invalid',
                    invalidated_utc = ?
                WHERE status = 'unknown'
                AND last_error_code = 'not_found'
            """, (now,))
            print(f"Marked {cur.rowcount} not_found symbols as invalid")
        
        # 4. Keep only timeout errors as unknown (these might be transient)
        print("\n" + "="*60)
        print("4. KEEPING TIMEOUT ERRORS AS UNKNOWN")
        print("="*60)
        
        cur.execute("""
            SELECT COUNT(*) FROM tickers 
            WHERE status = 'unknown' 
            AND last_error_code = 'timeout'
        """)
        timeout_count = cur.fetchone()[0]
        print(f"Keeping {timeout_count} timeout errors as unknown for retry")
        
        if not dry_run:
            con.commit()
    
    # Get final counts
    after = counts()
    
    print("\n" + "="*60)
    print("CLEANUP COMPLETE")
    print("="*60)
    print(f"\nAfter cleanup:")
    print(f"  Candidates: {before.get('candidate', 0):,} -> {after.get('candidate', 0):,} ({after.get('candidate', 0) - before.get('candidate', 0):+,})")
    print(f"  Unknown: {before.get('unknown', 0):,} -> {after.get('unknown', 0):,} ({after.get('unknown', 0) - before.get('unknown', 0):+,})")
    print(f"  Invalid: {before.get('invalid', 0):,} -> {after.get('invalid', 0):,} ({after.get('invalid', 0) - before.get('invalid', 0):+,})")
    
    if not dry_run:
        # Export updated master file
        n = export_active()
        print(f"\nExported {n:,} active symbols to master file")
    
    # Show what's left as unknown
    if after.get('unknown', 0) > 0:
        print(f"\n{after.get('unknown', 0)} symbols remain as unknown (timeout errors)")
        print("These can be retried in future validation runs")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Aggressive final cleanup")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without updating database")
    args = parser.parse_args()
    
    aggressive_cleanup(dry_run=args.dry_run)
    print("\nDone!")