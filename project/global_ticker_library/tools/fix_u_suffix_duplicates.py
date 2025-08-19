#!/usr/bin/env python3
"""
Fix -U vs -UN suffix duplicates in the Global Ticker Library.
Identifies tickers ending in -U that likely should be -UN (units).
"""
import sqlite3
import sys
from pathlib import Path
from typing import List, Tuple

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from global_ticker_library.gl_config import DB_PATH, MASTER_FILE
from global_ticker_library.registry import export_active

def find_u_suffix_pairs() -> List[Tuple[str, str, str, str]]:
    """
    Find all -U suffix tickers and their potential -UN counterparts.
    Returns list of (u_ticker, u_status, un_ticker, un_status)
    """
    pairs = []
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        
        # Find all tickers ending in -U (but not -USD)
        cur.execute("""
            SELECT symbol, status 
            FROM tickers 
            WHERE symbol LIKE '%-U' 
            AND symbol NOT LIKE '%-USD'
            ORDER BY symbol
        """)
        
        u_tickers = cur.fetchall()
        print(f"Found {len(u_tickers)} tickers ending in -U (excluding -USD)")
        
        for u_symbol, u_status in u_tickers:
            # Check if corresponding -UN version exists
            base = u_symbol[:-2]  # Remove -U
            un_symbol = base + "-UN"
            
            cur.execute("SELECT status FROM tickers WHERE symbol = ?", (un_symbol,))
            result = cur.fetchone()
            
            if result:
                un_status = result[0]
                pairs.append((u_symbol, u_status, un_symbol, un_status))
            else:
                # No -UN version exists
                pairs.append((u_symbol, u_status, None, None))
    
    return pairs

def analyze_pairs(pairs: List[Tuple[str, str, str, str]]) -> None:
    """Analyze and report on -U/-UN pairs."""
    print("\n" + "="*60)
    print("ANALYSIS OF -U SUFFIX TICKERS")
    print("="*60)
    
    # Count various scenarios
    both_active = []
    u_active_un_not = []
    un_active_u_not = []
    only_u_exists = []
    both_inactive = []
    
    for u_symbol, u_status, un_symbol, un_status in pairs:
        if un_symbol is None:
            only_u_exists.append(u_symbol)
        elif u_status == 'active' and un_status == 'active':
            both_active.append((u_symbol, un_symbol))
        elif u_status == 'active' and un_status != 'active':
            u_active_un_not.append((u_symbol, un_symbol, un_status))
        elif un_status == 'active' and u_status != 'active':
            un_active_u_not.append((u_symbol, u_status, un_symbol))
        else:
            both_inactive.append((u_symbol, u_status, un_symbol, un_status))
    
    print(f"\nBoth -U and -UN are active: {len(both_active)}")
    if both_active[:5]:
        print("  Examples:")
        for u, un in both_active[:5]:
            print(f"    {u} (active) vs {un} (active)")
    
    print(f"\n-U is active, -UN is not: {len(u_active_un_not)}")
    if u_active_un_not[:5]:
        print("  Examples:")
        for u, un, un_st in u_active_un_not[:5]:
            print(f"    {u} (active) vs {un} ({un_st})")
    
    print(f"\n-UN is active, -U is not: {len(un_active_u_not)}")
    if un_active_u_not[:5]:
        print("  Examples:")
        for u, u_st, un in un_active_u_not[:5]:
            print(f"    {u} ({u_st}) vs {un} (active)")
    
    print(f"\nOnly -U exists (no -UN): {len(only_u_exists)}")
    if only_u_exists[:5]:
        print("  Examples:", only_u_exists[:5])
    
    print(f"\nBoth inactive: {len(both_inactive)}")
    if both_inactive[:5]:
        print("  Examples:")
        for u, u_st, un, un_st in both_inactive[:5]:
            print(f"    {u} ({u_st}) vs {un} ({un_st})")
    
    # Recommend action
    print("\n" + "="*60)
    print("RECOMMENDED ACTIONS")
    print("="*60)
    
    if both_active:
        print(f"\n1. DUPLICATES: {len(both_active)} cases where both -U and -UN are active")
        print("   These are likely duplicates where -U should be marked invalid.")
        print("   Sample tickers to invalidate:")
        for u, un in both_active[:10]:
            print(f"     {u} (keep {un})")
    
    if u_active_un_not:
        print(f"\n2. WRONG SUFFIX: {len(u_active_un_not)} cases where -U is active but -UN exists")
        print("   These likely have the wrong suffix.")
        print("   Sample tickers to investigate:")
        for u, un, un_st in u_active_un_not[:10]:
            print(f"     {u} -> should be {un}?")

def fix_duplicates(dry_run: bool = True) -> None:
    """
    Fix duplicate -U/-UN tickers by marking -U versions as invalid
    when both exist and are active.
    """
    pairs = find_u_suffix_pairs()
    
    # Find cases where both are active
    to_invalidate = []
    for u_symbol, u_status, un_symbol, un_status in pairs:
        if un_symbol and u_status == 'active' and un_status == 'active':
            to_invalidate.append(u_symbol)
    
    if not to_invalidate:
        print("\nNo duplicate active -U/-UN pairs found.")
        return
    
    print(f"\nFound {len(to_invalidate)} -U tickers to invalidate (have active -UN counterparts)")
    
    if dry_run:
        print("\nDRY RUN - Would invalidate:")
        for symbol in to_invalidate[:20]:
            print(f"  {symbol}")
        if len(to_invalidate) > 20:
            print(f"  ... and {len(to_invalidate) - 20} more")
        print("\nRun with --fix to apply changes")
    else:
        print("\nInvalidating duplicate -U tickers...")
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            for symbol in to_invalidate:
                cur.execute("""
                    UPDATE tickers 
                    SET status = 'invalid', 
                        last_error_code = 'duplicate_suffix',
                        last_error_msg = 'Duplicate of -UN version'
                    WHERE symbol = ?
                """, (symbol,))
            con.commit()
        
        print(f"Invalidated {len(to_invalidate)} duplicate -U tickers")
        
        # Re-export master file
        print("\nRe-exporting master file...")
        n = export_active()
        print(f"Exported {n} active symbols to {MASTER_FILE.name}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fix -U vs -UN suffix duplicates")
    parser.add_argument("--fix", action="store_true", help="Apply fixes (default is dry run)")
    args = parser.parse_args()
    
    # Analyze pairs
    pairs = find_u_suffix_pairs()
    analyze_pairs(pairs)
    
    # Fix duplicates
    fix_duplicates(dry_run=not args.fix)

if __name__ == "__main__":
    main()