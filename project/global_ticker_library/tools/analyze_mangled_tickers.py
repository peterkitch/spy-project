#!/usr/bin/env python3
"""
Analyze potentially mangled tickers from previous canonicalization.
"""
import sqlite3
import sys
from pathlib import Path
from collections import Counter

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from global_ticker_library.gl_config import DB_PATH, MASTER_FILE

def analyze_mangled_tickers():
    """Find active tickers that may have been mangled by canonicalization."""
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        
        # Check for tickers where symbol != original
        cur.execute("""
            SELECT COUNT(*) 
            FROM tickers 
            WHERE status = 'active' AND symbol != original
        """)
        modified_count = cur.fetchone()[0]
        print(f"Active tickers where symbol was modified: {modified_count}")
        
        if modified_count > 0:
            print("\nExamples of modifications:")
            cur.execute("""
                SELECT symbol, original, canonical 
                FROM tickers 
                WHERE status = 'active' AND symbol != original
                LIMIT 20
            """)
            for symbol, original, canonical in cur.fetchall():
                print(f"  {symbol:20} <- was {original:20} (canon={canonical})")
        
        # Look for suspicious patterns
        print("\n" + "="*60)
        print("SUSPICIOUS PATTERNS IN ACTIVE TICKERS")
        print("="*60)
        
        # Pattern 1: -PX suffixes (likely mangled from -X)
        patterns = [
            ("%-PA", "Potential mangled A shares"),
            ("%-PB", "Potential mangled B shares"),
            ("%-PC", "Potential mangled C shares"),
            ("%-PD", "Potential mangled D shares"),
            ("%-PE", "Potential mangled E shares"),
            ("%-PF", "Potential mangled F shares"),
            ("%-PG", "Potential mangled G shares"),
            ("%-PH", "Potential mangled H shares"),
            ("%-PI", "Potential mangled I shares"),
            ("%-PJ", "Potential mangled J shares"),
            ("%-PK", "Potential mangled K shares"),
            ("%-PL", "Potential mangled L shares"),
            ("%-PM", "Potential mangled M shares"),
            ("%-PN", "Potential mangled N shares"),
            ("%-PO", "Potential mangled O shares"),
            ("%-PP", "Potential mangled P shares"),
            ("%-PQ", "Potential mangled Q shares"),
            ("%-PR", "Potential mangled R shares"),
            ("%-PS", "Potential mangled S shares"),
            ("%-PT", "Potential mangled T shares"),
            ("%-PU", "Potential mangled U shares"),
            ("%-PV", "Potential mangled V shares"),
            ("%-PW", "Potential mangled W shares"),
            ("%-PX", "Potential mangled X shares"),
            ("%-PY", "Potential mangled Y shares"),
            ("%-PZ", "Potential mangled Z shares"),
        ]
        
        suspicious = []
        for pattern, description in patterns:
            cur.execute("""
                SELECT COUNT(*) 
                FROM tickers 
                WHERE status = 'active' 
                AND symbol LIKE ?
                AND symbol NOT LIKE '%-USD%'
            """, (pattern,))
            count = cur.fetchone()[0]
            if count > 0:
                print(f"  {pattern}: {count} symbols - {description}")
                cur.execute("""
                    SELECT symbol, original 
                    FROM tickers 
                    WHERE status = 'active' 
                    AND symbol LIKE ?
                    AND symbol NOT LIKE '%-USD%'
                    LIMIT 5
                """, (pattern,))
                examples = cur.fetchall()
                for sym, orig in examples:
                    suspicious.append(sym)
                    print(f"    {sym} (original: {orig})")
        
        # Check master file
        print("\n" + "="*60)
        print("MASTER FILE ANALYSIS")
        print("="*60)
        
        if MASTER_FILE.exists():
            master_symbols = set(MASTER_FILE.read_text().strip().split(','))
            print(f"Total symbols in master file: {len(master_symbols):,}")
            
            # Check how many suspicious ones are in master
            suspicious_in_master = set(suspicious) & master_symbols
            if suspicious_in_master:
                print(f"\nSuspicious symbols in master file: {len(suspicious_in_master)}")
                print("Examples:")
                for sym in list(suspicious_in_master)[:10]:
                    print(f"  {sym}")
            
            # Count suffix patterns in master
            print("\nSuffix patterns in master file:")
            suffix_counts = Counter()
            for sym in master_symbols:
                if '-' in sym and not sym.startswith('^'):
                    suffix = sym.split('-')[-1]
                    if len(suffix) <= 3:  # Only short suffixes
                        suffix_counts[suffix] += 1
            
            for suffix, count in suffix_counts.most_common(20):
                print(f"  -{suffix}: {count:,} symbols")
        
        return suspicious

if __name__ == "__main__":
    suspicious = analyze_mangled_tickers()
    print(f"\n\nTotal potentially mangled tickers found: {len(suspicious)}")
    print("\nThese tickers should be re-validated to determine their correct form.")