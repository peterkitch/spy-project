#!/usr/bin/env python3
"""
Clean up misclassified rate_limit errors.
Based on diagnostic showing these are actually 404 errors for invalid symbols.
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from global_ticker_library.gl_config import DB_PATH
from global_ticker_library.registry import counts, export_active

def clean_misclassified():
    """Fix misclassified rate_limit errors."""
    
    print("="*60)
    print("CLEANING MISCLASSIFIED RATE LIMITS")
    print("="*60)
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        now = datetime.now(timezone.utc).isoformat()
        
        # Get current counts
        before = counts()
        print(f"\nBefore cleanup:")
        print(f"  Unknown: {before.get('unknown', 0):,}")
        print(f"  Invalid: {before.get('invalid', 0):,}")
        
        # Check how many rate_limit errors we have
        cur.execute("""
            SELECT COUNT(*) FROM tickers 
            WHERE status = 'unknown' 
            AND last_error_code = 'rate_limit'
        """)
        rate_limit_count = cur.fetchone()[0]
        
        print(f"\nFound {rate_limit_count} symbols marked as rate_limit")
        print("Based on diagnostics, these are actually 404 errors (invalid symbols)")
        
        if rate_limit_count > 0:
            # Update them to invalid
            cur.execute("""
                UPDATE tickers
                SET status = 'invalid',
                    last_error_code = 'not_found',
                    last_error_msg = 'Reclassified: was rate_limit, actually 404',
                    invalidated_utc = ?
                WHERE status = 'unknown'
                AND last_error_code = 'rate_limit'
            """, (now,))
            
            print(f"Reclassified {cur.rowcount} symbols from rate_limit to invalid")
            con.commit()
        
        # Get final counts
        after = counts()
        print(f"\nAfter cleanup:")
        print(f"  Unknown: {after.get('unknown', 0):,} ({after.get('unknown', 0) - before.get('unknown', 0):+,})")
        print(f"  Invalid: {after.get('invalid', 0):,} ({after.get('invalid', 0) - before.get('invalid', 0):+,})")
        
        # Export updated master file
        n = export_active()
        print(f"\nExported {n:,} active symbols to master_tickers.txt")

if __name__ == "__main__":
    clean_misclassified()
    print("\nDone!")