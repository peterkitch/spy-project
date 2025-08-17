"""
Wikipedia Indices Scraper
Extracts ticker symbols from S&P 500, NASDAQ-100, and other major indices
"""
from typing import Set
import pandas as pd
import re
from tqdm.auto import tqdm

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
DJIA_URL = "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average"

def _extract_symbols_anytable(url: str, candidate_names=("Symbol","Ticker","Trading Symbol","Code","Ticker symbol")) -> Set[str]:
    syms: Set[str] = set()
    tables = pd.read_html(url)  # returns list
    for df in tables:
        # Flatten/normalize headers to strings
        df.columns = [str(c).strip() for c in df.columns]
        # Try to find a likely symbol column
        lc = {c.lower(): c for c in df.columns}
        symbol_col = None
        for name in candidate_names:
            key = name.lower()
            if key in lc:
                symbol_col = lc[key]; break
        if not symbol_col:
            continue

        col = df[symbol_col].astype(str)
        for s in col:
            s = s.strip().upper()
            s = re.sub(r"\s*\[.*?\]$", "", s)  # drop footnotes
            if s and s.isascii() and len(s) <= 22:
                if not any(c in s for c in ['/', '\\', ' ', '\t', '\n', '(', ')']):
                    syms.add(s)
    return syms

def get_symbols() -> Set[str]:
    """Get all symbols from Wikipedia indices"""
    all_syms: Set[str] = set()
    
    # S&P 500
    try:
        tqdm.write("Fetching S&P 500 constituents...")
        sp500 = _extract_symbols_anytable(SP500_URL)
        all_syms.update(sp500)
        tqdm.write(f"  Found {len(sp500)} S&P 500 symbols")
    except Exception as e:
        tqdm.write(f"  Error with S&P 500: {str(e)[:100]}")
    
    # NASDAQ-100
    try:
        tqdm.write("Fetching NASDAQ-100 constituents...")
        ndx = _extract_symbols_anytable(NDX_URL)
        all_syms.update(ndx)
        tqdm.write(f"  Found {len(ndx)} NASDAQ-100 symbols")
    except Exception as e:
        tqdm.write(f"  Error with NASDAQ-100: {str(e)[:100]}")
    
    # Dow Jones Industrial Average
    try:
        tqdm.write("Fetching Dow 30 constituents...")
        djia = _extract_symbols_anytable(DJIA_URL)
        all_syms.update(djia)
        tqdm.write(f"  Found {len(djia)} Dow 30 symbols")
    except Exception as e:
        tqdm.write(f"  Error with Dow 30: {str(e)[:100]}")
    
    # Add some well-known indices manually
    indices = {
        "^GSPC",  # S&P 500 Index
        "^DJI",   # Dow Jones Index
        "^IXIC",  # NASDAQ Composite
        "^RUT",   # Russell 2000
        "^VIX",   # VIX
        "^TNX",   # 10-Year Treasury
        "^FTSE",  # FTSE 100
        "^N225",  # Nikkei 225
        "^HSI",   # Hang Seng
        "^GDAXI", # DAX
    }
    all_syms.update(indices)
    
    tqdm.write(f"Wikipedia indices total: {len(all_syms)} candidates")
    return all_syms

if __name__ == "__main__":
    # Test standalone
    symbols = get_symbols()
    print(f"Retrieved {len(symbols)} symbols")
    if symbols:
        sample = list(symbols)[:20]
        print(f"Sample: {sample}")