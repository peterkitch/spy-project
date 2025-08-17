"""
Yahoo Finance Crypto Screener
Collects all crypto tickers by paging Yahoo's predefined screeners.
No new dependencies required.
"""
from typing import Set, Tuple, Dict, Any, List
import time
import random
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm.auto import tqdm

from global_ticker_library.gl_config import DEFAULT_UA, REQUEST_TIMEOUT

# Predefined screener endpoint used by the Finance UI
_PREDEFINED_URL = "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved"

# The main US screener has the full list (~9,500+ symbols)
# Regional screeners have smaller subsets
_SCREENER_IDS = [
    "all_cryptocurrencies_us",  # Main list with 9,500+ symbols
    "all_cryptocurrencies_ca",  # Canadian subset (~277 symbols)
    "all_cryptocurrencies_eu",  # European subset (~277 symbols)
    "all_cryptocurrencies_gb",  # UK subset (~277 symbols)  
    "all_cryptocurrencies_au",  # Australian subset (~277 symbols)
    "all_cryptocurrencies_in",  # Indian subset (~277 symbols)
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


def _collect_for_screener(session: requests.Session, scr_id: str) -> Set[str]:
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
            tqdm.write(f"  {scr_id}: HTTP error: {msg[:120]}")
            break
        except Exception as e:
            tqdm.write(f"  {scr_id}: fetch error: {str(e)[:120]}")
            break

        if not quotes:
            break

        # Extract symbols of crypto quoteType only
        got = 0
        for q in quotes:
            if (q.get("quoteType") or "").upper() == "CRYPTOCURRENCY":
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
    Harvest crypto tickers from multiple Yahoo screeners and return the union.
    Only symbols with quoteType == 'CRYPTOCURRENCY' are kept.
    """
    session = _make_session()
    all_syms: Set[str] = set()

    tqdm.write("Fetching Yahoo Crypto screeners...")
    for scr_id in _SCREENER_IDS:
        try:
            syms = _collect_for_screener(session, scr_id)
            all_syms.update(syms)
            tqdm.write(f"  {scr_id:<26} +{len(syms)}")
        except Exception as e:
            tqdm.write(f"  {scr_id:<26} ERROR: {str(e)[:120]}")

    tqdm.write(f"Yahoo Crypto total: {len(all_syms)} candidates")
    return all_syms


if __name__ == "__main__":
    # Quick local test
    s = get_symbols()
    print(f"Retrieved {len(s)} crypto symbols")
    print(list(sorted(s))[:20])