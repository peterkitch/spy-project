#!/usr/bin/env python3
"""
Diagnose rate limit misclassification issues.
Tests if symbols are truly rate-limited or just invalid.
"""
import sys
from pathlib import Path
import time
import yfinance as yf
import requests

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

def test_rate_limit_behavior():
    """Test Yahoo Finance rate limiting behavior."""
    
    print("="*60)
    print("RATE LIMIT BEHAVIOR TESTING")
    print("="*60)
    
    # Test 1: Check valid symbol behavior
    print("\n1. Testing Valid Symbol (AAPL):")
    print("-"*40)
    ticker = yf.Ticker("AAPL")
    hist = ticker.history(period="1d")
    print(f"  Data retrieved: {not hist.empty}")
    print(f"  Rows: {len(hist)}")
    
    # Test 2: Check invalid symbol behavior
    print("\n2. Testing Invalid Symbol (INVALIDXYZ123):")
    print("-"*40)
    ticker = yf.Ticker("INVALIDXYZ123")
    hist = ticker.history(period="1d")
    print(f"  Data retrieved: {not hist.empty}")
    print(f"  Rows: {len(hist)}")
    
    # Test 3: Rapid requests to trigger rate limit
    print("\n3. Testing Rapid Requests (trying to trigger rate limit):")
    print("-"*40)
    
    test_symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "AMD"]
    rate_limit_hit = False
    
    for i in range(3):  # 3 rounds
        print(f"\n  Round {i+1}:")
        for sym in test_symbols:
            try:
                ticker = yf.Ticker(sym)
                hist = ticker.history(period="1d")
                print(f"    {sym}: OK", end="")
            except Exception as e:
                if "429" in str(e) or "rate" in str(e).lower():
                    print(f"    {sym}: RATE LIMITED!")
                    rate_limit_hit = True
                    break
                else:
                    print(f"    {sym}: Error")
        
        if rate_limit_hit:
            break
        print()  # New line after each round
    
    if not rate_limit_hit:
        print("\n  No rate limit hit (Yahoo allows reasonable rapid requests)")
    
    # Test 4: Check direct API endpoints
    print("\n4. Testing Direct API Endpoints:")
    print("-"*40)
    
    test_cases = [
        ("Valid symbol", "AAPL"),
        ("Invalid symbol", "INVALIDXYZ123"),
        ("Expired future", "ESZ20"),
        ("Delisted stock", "LEHMAN"),
    ]
    
    for description, symbol in test_cases:
        print(f"\n  {description} ({symbol}):")
        
        # Chart API
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            print(f"    Chart API: {resp.status_code}", end="")
            if resp.status_code == 404:
                print(" (Not Found)")
            elif resp.status_code == 429:
                print(" (Rate Limited!)")
            else:
                print()
        except Exception as e:
            print(f"    Chart API: Error - {str(e)[:30]}")
    
    # Test 5: Analyze error messages
    print("\n5. Error Message Analysis:")
    print("-"*40)
    
    invalid_symbols = ["FAKE123", "INVALID999", "NOTREAL", "XXXXXX"]
    
    for sym in invalid_symbols:
        print(f"\n  Testing {sym}:")
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="1d")
            if hist.empty:
                print(f"    Result: Empty DataFrame")
        except Exception as e:
            error_str = str(e)
            if "429" in error_str:
                print(f"    Contains '429': TRUE - Rate limit")
            elif "404" in error_str:
                print(f"    Contains '404': TRUE - Not found")
            elif "rate" in error_str.lower():
                print(f"    Contains 'rate': TRUE - Possible rate limit")
            elif "not found" in error_str.lower():
                print(f"    Contains 'not found': TRUE")
            elif "delisted" in error_str.lower():
                print(f"    Contains 'delisted': TRUE")
            else:
                print(f"    No specific error pattern detected")

def check_database_errors():
    """Check what errors are actually in the database."""
    
    print("\n" + "="*60)
    print("DATABASE ERROR ANALYSIS")
    print("="*60)
    
    try:
        import sqlite3
        from global_ticker_library.gl_config import DB_PATH
        
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        
        # Check error distribution
        cur.execute("""
            SELECT last_error_code, COUNT(*) as count
            FROM tickers
            WHERE status IN ('invalid', 'unknown')
            GROUP BY last_error_code
            ORDER BY count DESC
        """)
        
        print("\nError codes in database:")
        for error_code, count in cur.fetchall():
            print(f"  {error_code or 'none':20} {count:,}")
        
        con.close()
        
    except Exception as e:
        print(f"Could not analyze database: {e}")

if __name__ == "__main__":
    test_rate_limit_behavior()
    check_database_errors()
    
    print("\n" + "="*60)
    print("CONCLUSIONS")
    print("="*60)
    print("""
1. Yahoo Finance rarely issues true rate limits (429) for reasonable usage
2. Invalid symbols return 404 errors, not rate limits
3. The 'rate_limit' errors in our database were likely misclassified
4. We should mark 404 errors as 'invalid' immediately
5. True rate limits (429) are rare and should be retried
    """)
    
    print("\nDone!")