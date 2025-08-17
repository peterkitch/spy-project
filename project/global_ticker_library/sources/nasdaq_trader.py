"""
NASDAQ Trader Symbol Directory Scraper
Downloads official NASDAQ and other exchange listings
"""
import csv
import io
from typing import Set
import requests
from tqdm.auto import tqdm

from global_ticker_library.gl_config import DEFAULT_UA, REQUEST_TIMEOUT

NASDAQ_URLS = [
    "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
    "http://ftp.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
]
OTHER_URLS = [
    "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt",
    "http://ftp.nasdaqtrader.com/dynamic/symdir/otherlisted.txt",
]

def _fetch_text(urls) -> str:
    """Try a list of URLs, return first successful text, else ''."""
    for url in urls:
        try:
            r = requests.get(url, headers={"User-Agent": DEFAULT_UA}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception as e:
            tqdm.write(f"Error fetching {url}: {str(e)[:100]}")
    return ""

def _parse_pipe_table(text: str, symbol_col: str) -> Set[str]:
    """Parse pipe-delimited table format"""
    out: Set[str] = set()
    
    # Filter out footer lines and empty lines
    lines = [line for line in text.splitlines() if "|" in line and not line.startswith("File Creation")]
    if not lines:
        return out
    
    buf = io.StringIO("\n".join(lines))
    try:
        rdr = csv.DictReader(buf, delimiter="|")
        for row in rdr:
            sym = (row.get(symbol_col) or "").strip().upper()
            if not sym:
                continue
            # Skip test symbols
            if sym in {"TEST", "TEST-A", "TESTB", "TESTC", "TESTD"}:
                continue
            # Skip if contains invalid characters
            if any(c in sym for c in ['/', '\\', ' ', '\t', '\n']):
                continue
            out.add(sym)
    except Exception as e:
        tqdm.write(f"Error parsing table: {str(e)[:100]}")
    
    return out

def get_symbols() -> Set[str]:
    """Get all symbols from NASDAQ Trader"""
    syms: Set[str] = set()
    
    # Fetch NASDAQ listed
    try:
        tqdm.write("Fetching NASDAQ listings...")
        txt = _fetch_text(NASDAQ_URLS)
        if txt:
            nasdaq_syms = _parse_pipe_table(txt, "Symbol")
            syms.update(nasdaq_syms)
            tqdm.write(f"  Found {len(nasdaq_syms)} NASDAQ symbols")
    except Exception as e:
        tqdm.write(f"  Error with NASDAQ: {str(e)[:100]}")
    
    # Fetch other exchanges (NYSE, AMEX, etc.)
    try:
        tqdm.write("Fetching NYSE/AMEX listings...")
        txt = _fetch_text(OTHER_URLS)
        if txt:
            other_syms = _parse_pipe_table(txt, "ACT Symbol")
            syms.update(other_syms)
            tqdm.write(f"  Found {len(other_syms)} NYSE/AMEX symbols")
    except Exception as e:
        tqdm.write(f"  Error with other exchanges: {str(e)[:100]}")
    
    tqdm.write(f"NasdaqTrader total: {len(syms)} candidates")
    return syms

if __name__ == "__main__":
    # Test standalone
    symbols = get_symbols()
    print(f"Retrieved {len(symbols)} symbols")
    if symbols:
        sample = list(symbols)[:10]
        print(f"Sample: {sample}")