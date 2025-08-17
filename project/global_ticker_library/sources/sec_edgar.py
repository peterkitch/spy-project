"""
SEC EDGAR Company Tickers Scraper
Downloads the official SEC company tickers JSON
"""
from typing import Set
import requests
from tqdm.auto import tqdm

from global_ticker_library.gl_config import DEFAULT_UA, REQUEST_TIMEOUT

SEC_JSON = "https://www.sec.gov/files/company_tickers.json"

def get_symbols() -> Set[str]:
    """Get all ticker symbols from SEC EDGAR"""
    syms: Set[str] = set()
    
    try:
        tqdm.write("Fetching SEC company tickers...")
        
        # SEC requires a user agent
        headers = {
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json"
        }
        
        r = requests.get(SEC_JSON, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        
        # Extract tickers from the JSON structure
        # Format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        for key, company in data.items():
            ticker = company.get("ticker", "").strip().upper()
            if ticker and ticker.isascii():
                # Filter out obvious bad data
                if len(ticker) <= 10 and not any(c in ticker for c in ['/', '\\', ' ', '\t', '\n']):
                    syms.add(ticker)
        
        tqdm.write(f"  Found {len(syms)} SEC symbols")
        
    except requests.exceptions.RequestException as e:
        tqdm.write(f"  Error fetching SEC data: {str(e)[:100]}")
    except (KeyError, ValueError) as e:
        tqdm.write(f"  Error parsing SEC data: {str(e)[:100]}")
    except Exception as e:
        tqdm.write(f"  Unexpected error: {str(e)[:100]}")
    
    return syms

if __name__ == "__main__":
    # Test standalone
    symbols = get_symbols()
    print(f"Retrieved {len(symbols)} symbols")
    if symbols:
        sample = list(symbols)[:10]
        print(f"Sample: {sample}")