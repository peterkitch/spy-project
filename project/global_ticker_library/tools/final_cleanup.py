#!/usr/bin/env python3
"""
Final cleanup of remaining candidates and unknowns.
Based on testing, these are overwhelmingly invalid symbols.
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Tuple

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from global_ticker_library.gl_config import DB_PATH
from global_ticker_library.registry import counts, export_active, upsert_validation_results
from global_ticker_library.validator_yahoo import validate_symbols

def validate_and_classify_symbols(symbols: List[str], batch_size: int = 50) -> Tuple[List[str], List[str]]:
    """Validate symbols and return lists of valid and invalid ones."""
    valid_symbols = []
    invalid_symbols = []
    
    print(f"Validating {len(symbols)} symbols in batches of {batch_size}...")
    
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(symbols) + batch_size - 1) // batch_size
        
        print(f"  Batch {batch_num}/{total_batches}...", end=" ")
        
        try:
            results, agg = validate_symbols(batch, gentle=True, progress=False)
            
            active_count = 0
            invalid_count = 0
            
            for r in results:
                if r.get('status') == 'active':
                    valid_symbols.append(r['symbol'])
                    active_count += 1
                elif r.get('status') in ['invalid', 'unknown'] and r.get('error_code') == 'not_found':
                    invalid_symbols.append(r['symbol'])
                    invalid_count += 1
                elif r.get('status') == 'stale':
                    # Stale means it exists but data is old - not invalid
                    pass
            
            print(f"Active: {active_count}, Invalid: {invalid_count}")
            
            # Update database with results
            upsert_validation_results(results)
            
        except Exception as e:
            print(f"Error: {e}")
            # On error, assume symbols are invalid
            invalid_symbols.extend(batch)
    
    return valid_symbols, invalid_symbols

def cleanup_remaining_symbols(dry_run=False, sample_size=None):
    """Clean up remaining candidates and unknowns."""
    
    print("="*60)
    print("FINAL CLEANUP OF CANDIDATES AND UNKNOWNS")
    print("="*60)
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        now = datetime.now(timezone.utc).isoformat()
        
        # Get initial counts
        before = counts()
        print(f"\nBefore cleanup:")
        print(f"  Candidates: {before.get('candidate', 0):,}")
        print(f"  Unknown: {before.get('unknown', 0):,}")
        print(f"  Invalid: {before.get('invalid', 0):,}")
        print(f"  Active: {before.get('active', 0):,}")
        
        # 1. Process all remaining candidates
        print("\n" + "="*60)
        print("1. PROCESSING REMAINING CANDIDATES")
        print("="*60)
        
        cur.execute("""
            SELECT symbol FROM tickers 
            WHERE status = 'candidate'
            ORDER BY symbol
        """)
        candidates = [row[0] for row in cur.fetchall()]
        
        if sample_size and len(candidates) > sample_size:
            candidates = candidates[:sample_size]
            print(f"Testing sample of {sample_size} candidates...")
        else:
            print(f"Found {len(candidates)} candidates to process")
        
        if candidates:
            valid, invalid = validate_and_classify_symbols(candidates)
            print(f"\nResults: {len(valid)} valid, {len(invalid)} invalid")
            
            if valid:
                print(f"Valid candidates found: {valid[:10]}")
            
            if not dry_run and invalid:
                # Mark invalid candidates
                placeholders = ','.join('?' for _ in invalid)
                cur.execute(f"""
                    UPDATE tickers
                    SET status = 'invalid',
                        last_error_code = 'not_found',
                        last_error_msg = 'No data available',
                        invalidated_utc = ?
                    WHERE symbol IN ({placeholders})
                    AND status = 'candidate'
                """, [now] + invalid)
                print(f"Marked {cur.rowcount} candidates as invalid")
        
        # 2. Process unknowns with rate_limit errors (they're actually invalid)
        print("\n" + "="*60)
        print("2. PROCESSING RATE-LIMITED UNKNOWNS")
        print("="*60)
        
        cur.execute("""
            SELECT symbol FROM tickers 
            WHERE status = 'unknown'
            AND last_error_code = 'rate_limit'
            AND retry_count >= 2
            ORDER BY symbol
        """)
        rate_limited = [row[0] for row in cur.fetchall()]
        
        if sample_size and len(rate_limited) > sample_size:
            rate_limited = rate_limited[:sample_size]
            print(f"Testing sample of {sample_size} rate-limited symbols...")
        else:
            print(f"Found {len(rate_limited)} rate-limited unknowns with 2+ retries")
        
        if rate_limited:
            valid, invalid = validate_and_classify_symbols(rate_limited)
            print(f"\nResults: {len(valid)} valid, {len(invalid)} invalid")
            
            if valid:
                print(f"Valid rate-limited symbols found: {valid[:10]}")
            
            if not dry_run and invalid:
                # Mark invalid rate-limited symbols
                placeholders = ','.join('?' for _ in invalid)
                cur.execute(f"""
                    UPDATE tickers
                    SET status = 'invalid',
                        last_error_code = 'verified_invalid',
                        last_error_msg = 'Confirmed no data available',
                        invalidated_utc = ?
                    WHERE symbol IN ({placeholders})
                    AND status = 'unknown'
                """, [now] + invalid)
                print(f"Marked {cur.rowcount} rate-limited symbols as invalid")
        
        # 3. Process remaining unknowns with not_found errors
        print("\n" + "="*60)
        print("3. PROCESSING NOT_FOUND UNKNOWNS")
        print("="*60)
        
        cur.execute("""
            SELECT symbol FROM tickers 
            WHERE status = 'unknown'
            AND last_error_code = 'not_found'
            ORDER BY symbol
        """)
        not_found = [row[0] for row in cur.fetchall()]
        
        print(f"Found {len(not_found)} unknowns with not_found errors")
        
        if not_found and not dry_run:
            # These have already been confirmed as not found
            placeholders = ','.join('?' for _ in not_found)
            cur.execute(f"""
                UPDATE tickers
                SET status = 'invalid',
                    invalidated_utc = ?
                WHERE symbol IN ({placeholders})
                AND status = 'unknown'
            """, [now] + not_found)
            print(f"Marked {cur.rowcount} not_found symbols as invalid")
        
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
    print(f"  Active: {before.get('active', 0):,} -> {after.get('active', 0):,} ({after.get('active', 0) - before.get('active', 0):+,})")
    
    if not dry_run:
        # Export updated master file
        n = export_active()
        print(f"\nExported {n:,} active symbols to master file")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Final cleanup of candidates and unknowns")
    parser.add_argument("--dry-run", action="store_true", help="Test validation without updating database")
    parser.add_argument("--sample", type=int, help="Only process a sample of symbols for testing")
    args = parser.parse_args()
    
    cleanup_remaining_symbols(dry_run=args.dry_run, sample_size=args.sample)
    print("\nDone!")