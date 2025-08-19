#!/usr/bin/env python3
"""
Cleanup mangled duplicates from previous canonicalization.
Marks -PX versions as invalid when -X version is active.
"""
import sqlite3
import sys
from pathlib import Path
from collections import defaultdict

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from global_ticker_library.gl_config import DB_PATH, MASTER_FILE
from global_ticker_library.registry import export_active

def find_and_fix_mangled_duplicates(dry_run=False):
    """Find and fix mangled duplicates."""
    
    print("="*60)
    print("FINDING MANGLED DUPLICATES")
    print("="*60)
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        
        # Find all symbols with -P[A-Z] suffix
        cur.execute("""
            SELECT symbol, status, original, canonical 
            FROM tickers 
            WHERE symbol LIKE '%-P_'
            ORDER BY symbol
        """)
        p_symbols = cur.fetchall()
        
        print(f"Found {len(p_symbols)} symbols with -P[A-Z] suffix")
        
        # Check each for a corresponding non-P version
        duplicates = []
        for p_sym, p_status, p_orig, p_canon in p_symbols:
            # Extract base and suffix
            if '-P' in p_sym:
                base = p_sym.rsplit('-P', 1)[0]
                suffix = p_sym.rsplit('-P', 1)[1]
                
                # Check if the non-P version exists
                non_p_sym = f"{base}-{suffix}"
                cur.execute("SELECT symbol, status FROM tickers WHERE symbol = ?", (non_p_sym,))
                row = cur.fetchone()
                
                if row:
                    non_p_status = row[1]
                    duplicates.append((p_sym, p_status, non_p_sym, non_p_status))
                    print(f"  {p_sym:20} ({p_status:8}) <- mangled from -> {non_p_sym:20} ({non_p_status:8})")
        
        print(f"\nFound {len(duplicates)} potential mangled duplicates")
        
        # Decide which ones to invalidate
        to_invalidate = []
        for p_sym, p_status, non_p_sym, non_p_status in duplicates:
            # If the non-P version is active, invalidate the P version
            if non_p_status == 'active':
                to_invalidate.append(p_sym)
                print(f"  Will invalidate {p_sym} (keeping {non_p_sym} as active)")
            elif p_status == 'active' and non_p_status != 'active':
                # The P version is active but non-P isn't - this might be legitimate
                print(f"  WARNING: {p_sym} is active but {non_p_sym} is {non_p_status}")
        
        print(f"\n{len(to_invalidate)} symbols will be marked invalid")
        
        if not dry_run and to_invalidate:
            print("\nInvalidating mangled duplicates...")
            placeholders = ','.join('?' for _ in to_invalidate)
            cur.execute(f"""
                UPDATE tickers 
                SET status = 'invalid',
                    last_error_code = 'mangled_duplicate',
                    last_error_msg = 'Mangled from canonicalization'
                WHERE symbol IN ({placeholders})
            """, to_invalidate)
            con.commit()
            print(f"Invalidated {cur.rowcount} symbols")
        elif dry_run:
            print("\nDRY RUN - no changes made")
        
        # Also look for other suspicious patterns
        print("\n" + "="*60)
        print("OTHER SUSPICIOUS PATTERNS")
        print("="*60)
        
        # Check for -U vs -UN duplicates (common canonicalization error)
        cur.execute("""
            SELECT t1.symbol, t1.status, t2.symbol, t2.status
            FROM tickers t1
            INNER JOIN tickers t2 ON t1.symbol || 'N' = t2.symbol
            WHERE t1.symbol LIKE '%-U'
            ORDER BY t1.symbol
        """)
        u_duplicates = cur.fetchall()
        
        if u_duplicates:
            print(f"\nFound {len(u_duplicates)} -U vs -UN duplicates:")
            for sym1, status1, sym2, status2 in u_duplicates:
                print(f"  {sym1:20} ({status1:8}) vs {sym2:20} ({status2:8})")
                
                # Generally -UN is correct for units, -U alone is wrong
                if not dry_run and status1 != 'invalid' and status2 == 'active':
                    cur.execute("""
                        UPDATE tickers 
                        SET status = 'invalid',
                            last_error_code = 'wrong_suffix',
                            last_error_msg = 'Should be -UN for units'
                        WHERE symbol = ?
                    """, (sym1,))
                    print(f"    -> Invalidated {sym1}")
        
        # Check for -W vs -WS duplicates (warrants)
        cur.execute("""
            SELECT t1.symbol, t1.status, t2.symbol, t2.status
            FROM tickers t1
            INNER JOIN tickers t2 ON t1.symbol || 'S' = t2.symbol
            WHERE t1.symbol LIKE '%-W'
            ORDER BY t1.symbol
        """)
        w_duplicates = cur.fetchall()
        
        if w_duplicates:
            print(f"\nFound {len(w_duplicates)} -W vs -WS duplicates:")
            for sym1, status1, sym2, status2 in w_duplicates:
                print(f"  {sym1:20} ({status1:8}) vs {sym2:20} ({status2:8})")
                
                # Generally -WS is correct for warrants, -W alone might be wrong
                if not dry_run and status1 != 'invalid' and status2 == 'active':
                    cur.execute("""
                        UPDATE tickers 
                        SET status = 'invalid',
                            last_error_code = 'wrong_suffix',
                            last_error_msg = 'Should be -WS for warrants'
                        WHERE symbol = ?
                    """, (sym1,))
                    print(f"    -> Invalidated {sym1}")
        
        if not dry_run:
            con.commit()

def cleanup_master_file():
    """Remove mangled tickers from master file."""
    
    print("\n" + "="*60)
    print("CLEANING MASTER FILE")
    print("="*60)
    
    if not MASTER_FILE.exists():
        print("Master file not found")
        return
    
    # Get current active symbols from database
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        cur.execute("SELECT symbol FROM tickers WHERE status = 'active' ORDER BY symbol")
        active_symbols = set(row[0] for row in cur.fetchall())
    
    # Read master file
    master_text = MASTER_FILE.read_text()
    master_symbols = set(master_text.strip().split(','))
    
    print(f"Master file has {len(master_symbols):,} symbols")
    print(f"Database has {len(active_symbols):,} active symbols")
    
    # Find symbols in master that are not active
    in_master_not_active = master_symbols - active_symbols
    
    if in_master_not_active:
        print(f"\n{len(in_master_not_active)} symbols in master file are not active:")
        
        # Check what status they have
        with sqlite3.connect(DB_PATH) as con:
            cur = con.cursor()
            placeholders = ','.join('?' for _ in in_master_not_active)
            cur.execute(f"""
                SELECT symbol, status 
                FROM tickers 
                WHERE symbol IN ({placeholders})
                ORDER BY symbol
            """, list(in_master_not_active))
            
            status_breakdown = defaultdict(list)
            for sym, status in cur.fetchall():
                status_breakdown[status or 'not_in_db'].append(sym)
            
            # Check for symbols not in DB at all
            in_db = set(row[0] for row in cur.execute("SELECT symbol FROM tickers WHERE symbol IN ({})".format(placeholders), list(in_master_not_active)))
            not_in_db = in_master_not_active - in_db
            if not_in_db:
                status_breakdown['not_in_db'].extend(not_in_db)
            
            for status, syms in status_breakdown.items():
                print(f"  {status}: {len(syms)} symbols")
                for sym in list(syms)[:10]:  # Show first 10
                    print(f"    {sym}")
                if len(syms) > 10:
                    print(f"    ... and {len(syms)-10} more")
    
    # Export clean master file
    print("\nExporting clean master file...")
    n = export_active()
    print(f"Exported {n:,} active symbols to {MASTER_FILE.name}")
    
    # Verify
    new_master = set(MASTER_FILE.read_text().strip().split(','))
    removed = master_symbols - new_master
    if removed:
        print(f"\nRemoved {len(removed)} symbols from master file:")
        for sym in list(removed)[:20]:
            print(f"  {sym}")
        if len(removed) > 20:
            print(f"  ... and {len(removed)-20} more")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Cleanup mangled duplicates from canonicalization")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying database")
    args = parser.parse_args()
    
    find_and_fix_mangled_duplicates(dry_run=args.dry_run)
    if not args.dry_run:
        cleanup_master_file()
    
    print("\nDone!")