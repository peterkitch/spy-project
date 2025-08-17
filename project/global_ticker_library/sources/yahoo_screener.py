"""
Yahoo Finance Multi-Asset Screener (ETF/MutualFund/Equity)
Uses predefined screeners to harvest symbols across asset classes.
No authentication required - uses GET requests to public endpoints.
"""
from typing import Set, Tuple, Dict, Any, List
import time
import random
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm.auto import tqdm

from global_ticker_library.gl_config import DEFAULT_UA, REQUEST_TIMEOUT

# Predefined screener endpoint (GET)
_PREDEFINED_URL = "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved"

# Working predefined screener IDs organized by asset class
_SCREENER_CONFIGS = [
    # ETFs
    ("etf", "ETF", None),  # 887 ETFs
    
    # Mutual Funds
    ("portfolio_anchors", "MUTUALFUND", None),  # 282 funds
    ("conservative_foreign_funds", "MUTUALFUND", None),  # 176 funds
    
    # Equity Screeners - Dynamic lists that catch trending/new tickers
    ("most_actives", "EQUITY", None),  # ~278 most traded
    ("day_gainers", "EQUITY", None),  # ~81 top gainers
    ("day_losers", "EQUITY", None),  # ~94 top losers
    ("most_shorted_stocks", "EQUITY", None),  # ~3,857 heavily shorted
    ("undervalued_growth_stocks", "EQUITY", None),  # ~151 value picks
    ("growth_technology_stocks", "EQUITY", None),  # ~11 growth tech
    ("aggressive_small_caps", "EQUITY", None),  # ~303 small caps
    ("small_cap_gainers", "EQUITY", None),  # ~59 small cap winners
    
    # Sector-specific Equities (Morningstar categories)
    ("ms_technology", "EQUITY", None),  # ~411 tech stocks
    ("ms_industrials", "EQUITY", None),  # ~417 industrials
    ("ms_basic_materials", "EQUITY", None),  # ~153 materials
    ("ms_financial_services", "EQUITY", None),  # ~1,079 financials
    ("ms_energy", "EQUITY", None),  # ~184 energy
    ("ms_utilities", "EQUITY", None),  # ~103 utilities
    ("ms_healthcare", "EQUITY", None),  # ~331 healthcare
    ("ms_consumer_defensive", "EQUITY", None),  # ~141 consumer staples
    ("ms_consumer_cyclical", "EQUITY", None),  # ~343 consumer discretionary
    ("ms_real_estate", "EQUITY", None),  # ~332 REITs
    ("ms_communication_services", "EQUITY", None),  # ~149 communication
]

# Conservative default page size; we adapt downward if Yahoo says "size is too large"
_DEFAULT_COUNT = 250
_MIN_COUNT = 50


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json",
        "Referer": "https://finance.yahoo.com/",
    })
    retry = Retry(
        total=3,
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def _fetch_page(session: requests.Session, scr_id: str, count: int, start: int) -> Tuple[List[Dict[str, Any]], int]:
    params = {"scrIds": scr_id, "count": count, "start": start}
    r = session.get(_PREDEFINED_URL, params=params, timeout=REQUEST_TIMEOUT)
    # Respect 429s by short sleep + raise_for_status (will be retried by session if eligible)
    if r.status_code == 429:
        time.sleep(1.0 + random.random())
    r.raise_for_status()
    data = r.json() if r.content else {}
    result = (data.get("finance") or {}).get("result") or []
    if not result:
        return [], 0
    block = result[0]
    quotes = block.get("quotes") or []
    total = int(block.get("total") or len(quotes))
    return quotes, total


def _collect_for_screener(session: requests.Session, scr_id: str, expected_type: str = None) -> Set[str]:
    syms: Set[str] = set()
    count = _DEFAULT_COUNT
    start = 0
    seen = 0

    while True:
        try:
            quotes, total = _fetch_page(session, scr_id, count, start)
        except requests.HTTPError as e:
            msg = str(e)
            if "size is too large" in msg.lower() and count > _MIN_COUNT:
                count = max(_MIN_COUNT, count // 2)
                tqdm.write(f"  {scr_id}: reducing page size -> {count} due to 'size is too large'")
                # brief pause then retry this page
                time.sleep(0.4)
                continue
            # Skip 404s silently (screener might not exist)
            if "404" not in msg:
                tqdm.write(f"  {scr_id}: HTTP error: {msg[:120]}")
            break
        except Exception as e:
            tqdm.write(f"  {scr_id}: fetch error: {str(e)[:120]}")
            break

        if not quotes:
            break

        # Extract symbols, optionally filtering by quoteType
        got = 0
        for q in quotes:
            if expected_type is None or (q.get("quoteType") or "").upper() == expected_type:
                sym = (q.get("symbol") or "").strip().upper()
                if sym:
                    syms.add(sym)
                    got += 1

        seen += len(quotes)
        start += len(quotes)

        # pacing to be nice to Yahoo (esp. large harvests)
        time.sleep(0.15)

        if total and seen >= total:
            break

    return syms


def get_symbols() -> Set[str]:
    """
    Harvest symbols from multiple predefined Yahoo screeners.
    Covers ETFs, Mutual Funds, and various Equity categories.
    """
    session = _make_session()
    all_syms: Set[str] = set()
    
    # Group by type for summary
    type_counts = {"ETF": 0, "MUTUALFUND": 0, "EQUITY": 0}

    tqdm.write("Fetching Yahoo Screeners (ETF/MutualFund/Equity)...")
    for scr_id, expected_type, desc in _SCREENER_CONFIGS:
        try:
            syms = _collect_for_screener(session, scr_id, expected_type)
            if syms:
                all_syms.update(syms)
                type_counts[expected_type] = type_counts.get(expected_type, 0) + len(syms)
                tqdm.write(f"  {scr_id:<30} +{len(syms)}")
        except Exception as e:
            # Skip silently for non-existent screeners
            if "404" not in str(e):
                tqdm.write(f"  {scr_id:<30} ERROR: {str(e)[:120]}")

    # Summary
    tqdm.write(f"Yahoo Screener summary:")
    for qtype, count in sorted(type_counts.items()):
        if count > 0:
            tqdm.write(f"  {qtype}: {count} symbols")
    tqdm.write(f"Yahoo Screener total: {len(all_syms)} candidates (deduplicated)")
    
    return all_syms


if __name__ == "__main__":
    s = get_symbols()
    print(f"\nRetrieved {len(s)} unique symbols")
    # Show sample by type
    etfs = [sym for sym in s if "-" not in sym and len(sym) <= 5][:5]
    funds = [sym for sym in s if len(sym) == 5 and sym[-1] == "X"][:5]
    print(f"Sample ETFs: {etfs}")
    print(f"Sample funds: {funds}")
    print(f"First 20 overall: {list(sorted(s))[:20]}")