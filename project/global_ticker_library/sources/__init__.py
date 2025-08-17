"""
Sources Package
Aggregates ticker symbols from multiple data sources
"""
from typing import Set
from tqdm.auto import tqdm

def gather_all() -> Set[str]:
    """
    Gather symbols from core curated sources + compact global seeds.
    Returns unified set of candidate symbols.
    """
    from . import yahoo_crypto, yahoo_screener, nasdaq_trader, sec_edgar, wikipedia_indices, seeds

    all_symbols: Set[str] = set()
    sources = [
        ("Yahoo Crypto", yahoo_crypto),
        ("Yahoo Screener", yahoo_screener),
        ("NASDAQ Trader", nasdaq_trader),
        ("SEC EDGAR", sec_edgar),
        ("Wikipedia Indices", wikipedia_indices),
        ("Global Seeds", seeds),
    ]

    tqdm.write("\n" + "="*60)
    tqdm.write("Gathering symbols from curated sources...")
    tqdm.write("="*60)

    for name, module in sources:
        try:
            symbols = module.get_symbols()
            all_symbols.update(symbols)
            tqdm.write(f"  {name:<20} +{len(symbols)}")
        except Exception as e:
            tqdm.write(f"  {name:<20} ERROR: {str(e)[:100]}")

    # Clean and normalize
    cleaned = {s.strip().upper() for s in all_symbols if s and s.strip()}

    tqdm.write("="*60)
    tqdm.write(f"Total unique candidates: {len(cleaned)}")
    tqdm.write("="*60 + "\n")

    return cleaned


def gather_optional() -> Set[str]:
    """
    Optional/extended sources (Day-2: yahoo_screeners, finviz, community_feeds).
    """
    return set()