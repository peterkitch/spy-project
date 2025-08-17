"""
Global Seeds for Crypto, FX, Futures, and World Indices
Compact, high-signal seeds for true global coverage on Day-1
"""
from typing import Set

# Compact, high-signal seeds (keep small for Day-1)
CRYPTO: Set[str] = {
    "BTC-USD","ETH-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD",
    "TON-USD","AVAX-USD","DOT-USD","LINK-USD","MATIC-USD","ATOM-USD","UNI-USD","XLM-USD",
    "ETC-USD","NEAR-USD","APT-USD","ICP-USD","AAVE-USD","ALGO-USD","SUI-USD","RNDR-USD",
    "AR-USD","OP-USD"
}

FX: Set[str] = {
    "EURUSD=X","USDJPY=X","GBPUSD=X","AUDUSD=X","USDCAD=X","USDCHF=X","NZDUSD=X",
    "EURJPY=X","EURGBP=X","EURCHF=X","AUDJPY=X","GBPJPY=X","CADJPY=X","CHFJPY=X",
    "USDHKD=X","USDCNH=X"
}

FUTURES: Set[str] = {
    "ES=F","NQ=F","YM=F","RTY=F",    # US index futures
    "CL=F","NG=F","GC=F","SI=F","HG=F",  # Energy/Metals
    "ZC=F","ZW=F","ZS=F","ZO=F",         # Grains
    "ZB=F","ZN=F","ZF=F","ZT=F"          # UST futures
}

WORLD_INDICES: Set[str] = {
    "^GSPC","^DJI","^IXIC","^RUT","^VIX","^TNX",
    "^FTSE","^N225","^HSI","^GDAXI","^FCHI","^AEX","^SSMI","^BSESN","^NSEI","^KS11",
    "^SSEC","^STOXX50E","^BVSP","^TA125.TA"
}

def get_symbols() -> Set[str]:
    """Return all global seed symbols"""
    return set().union(CRYPTO, FX, FUTURES, WORLD_INDICES)