#!/usr/bin/env python3
"""
Restore valid cryptocurrency pairs that were incorrectly marked as invalid.
Many crypto symbols start with numbers (like 0XBTC-USD, 1INCH-USD).
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from global_ticker_library.gl_config import DB_PATH
from global_ticker_library.registry import counts, export_active, upsert_validation_results
from global_ticker_library.validator_yahoo import validate_symbols

def restore_crypto_pairs(dry_run=False):
    """Restore cryptocurrency pairs that were incorrectly invalidated."""
    
    print("="*60)
    print("RESTORE VALID CRYPTOCURRENCY PAIRS")
    print("="*60)
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        
        # Find all -USD pairs that were marked invalid with "invalid_format" error
        cur.execute("""
            SELECT symbol 
            FROM tickers 
            WHERE status = 'invalid'
            AND symbol LIKE '%-USD'
            AND last_error_code = 'invalid_format'
        """)
        usd_pairs = [row[0] for row in cur.fetchall()]
        
        print(f"Found {len(usd_pairs)} USD pairs marked as invalid")
        
        if not usd_pairs:
            print("No USD pairs to restore")
            return
        
        # Also check for other crypto suffixes
        crypto_suffixes = ['-USD', '-USDT', '-BTC', '-ETH', '-EUR', '-GBP', '-USDC', '-BUSD']
        
        all_crypto_candidates = set()
        for suffix in crypto_suffixes:
            cur.execute("""
                SELECT symbol 
                FROM tickers 
                WHERE status = 'invalid'
                AND symbol LIKE ?
                AND (last_error_code = 'invalid_format' 
                     OR last_error_code = 'validation_failed')
            """, (f'%{suffix}',))
            for row in cur.fetchall():
                all_crypto_candidates.add(row[0])
        
        print(f"Found {len(all_crypto_candidates)} total crypto candidates to check")
        
        if dry_run:
            print("\nDRY RUN - Testing first 20 symbols:")
            test_batch = list(all_crypto_candidates)[:20]
            
            print("\nValidating sample batch...")
            results, agg = validate_symbols(test_batch, gentle=True, progress=False)
            
            valid_count = sum(1 for r in results if r.get('status') == 'active')
            print(f"\nResults: {valid_count}/{len(test_batch)} are valid")
            
            for r in results:
                if r.get('status') == 'active':
                    print(f"  {r['symbol']:20} VALID - Has recent data")
        else:
            # Process in batches
            batch_size = 50
            restored_count = 0
            
            crypto_list = list(all_crypto_candidates)
            for i in range(0, len(crypto_list), batch_size):
                batch = crypto_list[i:i+batch_size]
                print(f"\nProcessing batch {i//batch_size + 1}/{(len(crypto_list) + batch_size - 1)//batch_size}")
                
                # Validate the batch
                results, agg = validate_symbols(batch, gentle=True, progress=False)
                
                # Update database with results
                n_active, n_stale, n_invalid, n_unknown, additions, removals = upsert_validation_results(results)
                
                restored_count += n_active
                print(f"  Restored {n_active} active, {n_stale} stale, kept {n_invalid} invalid")
                
                if additions:
                    print(f"  Restored: {', '.join(additions[:5])}{' ...' if len(additions) > 5 else ''}")
            
            con.commit()
            
            # Export updated master file
            n = export_active()
            print(f"\n{restored_count} symbols restored as active")
            print(f"Exported {n:,} active symbols to master file")

def check_numeric_patterns():
    """Analyze what numeric patterns exist in valid symbols."""
    
    print("\n" + "="*60)
    print("NUMERIC PATTERN ANALYSIS")
    print("="*60)
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        
        # Check active symbols that start with numbers
        cur.execute("""
            SELECT symbol, quote_type
            FROM tickers 
            WHERE status = 'active'
            AND symbol GLOB '[0-9]*'
            LIMIT 20
        """)
        
        print("\nActive symbols starting with numbers:")
        for sym, qtype in cur.fetchall():
            print(f"  {sym:20} Type: {qtype}")
        
        # Check patterns
        cur.execute("""
            SELECT 
                CASE 
                    WHEN symbol LIKE '%-USD' THEN 'Crypto USD'
                    WHEN symbol LIKE '%-USDT' THEN 'Crypto USDT'
                    WHEN symbol LIKE '%-BTC' THEN 'Crypto BTC'
                    WHEN symbol LIKE '%-ETH' THEN 'Crypto ETH'
                    WHEN symbol LIKE '%.%' THEN 'Has dot'
                    WHEN symbol GLOB '[0-9]*-*' THEN 'Number with dash'
                    ELSE 'Pure number'
                END as pattern,
                COUNT(*) as count
            FROM tickers 
            WHERE status = 'active'
            AND symbol GLOB '[0-9]*'
            GROUP BY pattern
            ORDER BY count DESC
        """)
        
        print("\nPatterns in active symbols starting with numbers:")
        for pattern, count in cur.fetchall():
            print(f"  {pattern:20} {count:,}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Restore valid cryptocurrency pairs")
    parser.add_argument("--dry-run", action="store_true", help="Test validation without updating database")
    parser.add_argument("--analyze", action="store_true", help="Analyze numeric patterns in symbols")
    args = parser.parse_args()
    
    if args.analyze:
        check_numeric_patterns()
    else:
        restore_crypto_pairs(dry_run=args.dry_run)
    
    print("\nDone!")