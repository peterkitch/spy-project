#!/usr/bin/env python3
"""
Process stuck candidates that were validated but remain as candidates.
Force re-validation to properly classify them.
"""
import sqlite3
import sys
from pathlib import Path
from typing import List

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from global_ticker_library.gl_config import DB_PATH, PROGRESS_FILE
from global_ticker_library.registry import (
    init_db, counts, upsert_validation_results, export_active, write_progress, clear_progress
)
from global_ticker_library.validator_yahoo import validate_symbols

def find_stuck_candidates(limit: int = None) -> List[str]:
    """Find candidates that have been verified but not properly classified."""
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        
        # Find candidates that have been verified (have last_verified_utc)
        # but are still marked as candidates
        query = """
            SELECT symbol, last_verified_utc, retry_count, last_error_code
            FROM tickers 
            WHERE status = 'candidate' 
            AND last_verified_utc IS NOT NULL
            ORDER BY last_verified_utc DESC
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        cur.execute(query)
        results = cur.fetchall()
        
        print(f"Found {len(results)} stuck candidates (verified but still 'candidate' status)")
        
        if results and len(results) <= 20:
            print("\nStuck candidates:")
            for sym, last_verified, retry_count, error_code in results:
                print(f"  {sym:20} verified: {last_verified or 'never':20} retries: {retry_count} error: {error_code or 'none'}")
        
        return [row[0] for row in results]

def process_stuck_candidates(symbols: List[str], batch_size: int = 100, gentle: bool = True):
    """Force re-validation of stuck candidates."""
    
    if not symbols:
        print("No stuck candidates to process")
        return
    
    print(f"\nProcessing {len(symbols):,} stuck candidates...")
    print("="*60)
    
    # Initial counts
    before = counts()
    
    total_active = 0
    total_stale = 0
    total_invalid = 0
    total_unknown = 0
    
    # Process in batches
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(symbols) + batch_size - 1) // batch_size
        
        print(f"\nBatch {batch_num}/{total_batches}: Processing {len(batch)} symbols...")
        
        # Write progress
        write_progress({
            "status": "running",
            "phase": "stuck_candidates",
            "total": len(symbols),
            "done": i,
            "message": f"Processing stuck candidates... {i}/{len(symbols)}",
            "batch_number": batch_num,
            "total_batches": total_batches,
            "cumulative_active": total_active,
            "cumulative_stale": total_stale,
            "cumulative_invalid": total_invalid,
            "cumulative_unknown": total_unknown,
        })
        
        try:
            # Validate batch
            results, agg = validate_symbols(batch, gentle=gentle, progress=False)
            
            # Update database
            n_active, n_stale, n_invalid, n_unknown, additions, removals = upsert_validation_results(results)
            
            # Update totals
            total_active += n_active
            total_stale += n_stale
            total_invalid += n_invalid
            total_unknown += n_unknown
            
            # Show batch results
            print(f"  Active: {n_active}, Stale: {n_stale}, Invalid: {n_invalid}, Unknown: {n_unknown}")
            
            if additions:
                print(f"  New active: {', '.join(additions[:5])}{' ...' if len(additions) > 5 else ''}")
            
            # Show any errors
            if agg.get('rate_limit', 0) > 0:
                print(f"  [WARNING] Rate limits: {agg['rate_limit']}")
            if agg.get('timeout', 0) > 0:
                print(f"  [WARNING] Timeouts: {agg['timeout']}")
                
        except Exception as e:
            print(f"  [ERROR] Batch failed: {e}")
            total_unknown += len(batch)
    
    # Clear progress
    clear_progress()
    
    # Final counts
    after = counts()
    
    print("\n" + "="*60)
    print("STUCK CANDIDATES PROCESSING COMPLETE")
    print("="*60)
    print(f"\nProcessed {len(symbols):,} stuck candidates:")
    print(f"  Active:  {total_active:,}")
    print(f"  Stale:   {total_stale:,}")
    print(f"  Invalid: {total_invalid:,}")
    print(f"  Unknown: {total_unknown:,}")
    
    print(f"\nDatabase changes:")
    print(f"  Candidates: {before['candidate']:,} -> {after['candidate']:,} ({after['candidate'] - before['candidate']:+,})")
    print(f"  Active:     {before['active']:,} -> {after['active']:,} ({after['active'] - before['active']:+,})")
    print(f"  Stale:      {before['stale']:,} -> {after['stale']:,} ({after['stale'] - before['stale']:+,})")
    print(f"  Invalid:    {before['invalid']:,} -> {after['invalid']:,} ({after['invalid'] - before['invalid']:+,})")
    print(f"  Unknown:    {before.get('unknown', 0):,} -> {after.get('unknown', 0):,} ({after.get('unknown', 0) - before.get('unknown', 0):+,})")
    
    # Export if we found new active symbols
    if after['active'] > before['active']:
        n = export_active()
        print(f"\nExported {n:,} active symbols to master file")

def check_for_duplicate_candidates():
    """Check if candidates are already in the database with different status."""
    
    print("\n" + "="*60)
    print("CHECKING FOR DUPLICATE CANDIDATES")
    print("="*60)
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        
        # Check if any candidates have the same canonical form as active symbols
        cur.execute("""
            SELECT c.symbol, c.canonical, a.symbol, a.status
            FROM tickers c
            INNER JOIN tickers a ON c.canonical = a.canonical
            WHERE c.status = 'candidate' 
            AND a.status IN ('active', 'stale', 'invalid')
            AND c.symbol != a.symbol
            LIMIT 50
        """)
        
        duplicates = cur.fetchall()
        
        if duplicates:
            print(f"Found {len(duplicates)} candidates that match existing symbols:")
            for c_sym, c_canon, a_sym, a_status in duplicates:
                print(f"  Candidate: {c_sym:20} matches {a_sym:20} ({a_status})")
                
            # Mark these candidates as invalid
            duplicate_symbols = [row[0] for row in duplicates]
            placeholders = ','.join('?' for _ in duplicate_symbols)
            cur.execute(f"""
                UPDATE tickers 
                SET status = 'invalid',
                    last_error_code = 'duplicate',
                    last_error_msg = 'Duplicate of existing symbol'
                WHERE symbol IN ({placeholders})
            """, duplicate_symbols)
            
            if cur.rowcount > 0:
                con.commit()
                print(f"\nMarked {cur.rowcount} duplicate candidates as invalid")
        else:
            print("No duplicate candidates found")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Process stuck candidates")
    parser.add_argument("--limit", type=int, help="Limit number of candidates to process")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for validation")
    parser.add_argument("--fast", action="store_true", help="Use fast mode (not gentle)")
    parser.add_argument("--check-duplicates", action="store_true", help="Only check for duplicates")
    args = parser.parse_args()
    
    init_db()
    
    if args.check_duplicates:
        check_for_duplicate_candidates()
    else:
        # Find and process stuck candidates
        stuck = find_stuck_candidates(limit=args.limit)
        if stuck:
            process_stuck_candidates(stuck, batch_size=args.batch_size, gentle=not args.fast)
        
        # Also check for duplicates
        check_for_duplicate_candidates()
    
    print("\nDone!")