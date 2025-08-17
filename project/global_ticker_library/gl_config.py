"""
Global Ticker Library Configuration
Optimized settings for batch validation and efficient processing
"""
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Essential files only
DB_PATH = DATA_DIR / "registry.db"
MASTER_FILE = DATA_DIR / "master_tickers.txt"
REMOVALS_LOG_FILE = DATA_DIR / "removals_log.txt"

# Optional user input file (created by user when needed)
MANUAL_FILE = DATA_DIR / "manual_input.txt"

# Temporary staging (deleted after use)
STAGING_FILE = DATA_DIR / "staging_bucket.txt"

# Progress tracking for CLI/Dashboard coordination
PROGRESS_FILE = DATA_DIR / "cli_progress.json"

# Deprecated - these are no longer generated
ADDITIONS_LOG_FILE = None  # No longer needed
SCRAPED_ACTIVE_FILE = None  # No longer needed

# Validation & cleanup
BATCH_SIZE = 200           # Yahoo quote batch size (optimal for API)
REQUEST_TIMEOUT = 12       # seconds
INTER_BATCH_SLEEP = 0.25   # seconds between quote batches (0.2-0.3 range)
STALE_DAYS = 30            # consider stale if no market time within N days
REMOVAL_CONFIRMATIONS = 2  # stale strikes required before invalidation
CACHE_TTL_HOURS = 24       # Don't re-validate ACTIVE symbols within this period

# Retry windows (as per external help recommendations)
UNKNOWN_RETRY_MINUTES = 60      # Retry unknown status after 60 minutes
STALE_RECHECK_DAYS = 3          # Retry stale status after 3 days  
INVALID_RECHECK_DAYS = 30       # Retry invalid status after 30 days

# Retry logic
MAX_RETRIES = 3            # Maximum retry attempts
RETRY_BACKOFF_BASE = 0.5   # Initial backoff in seconds
RETRY_BACKOFF_MAX = 2.0    # Maximum backoff in seconds

# Network
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36; "
    "contact=provendelusion@gmail.com"
)

# International support
VALID_SUFFIXES = {
    '.L', '.TO', '.V', '.AX', '.HK', '.NS', '.BO', '.PA', '.AS', 
    '.DE', '.SW', '.MI', '.SA', '.KS', '.KQ', '.SS', '.SZ', '.NZ'
}

# Special namespaces
SPECIAL_PREFIXES = {'^', '='}  # Indices and futures/FX
CRYPTO_SUFFIX = '-USD'  # Common crypto pairs

# Dashboard
DASH_PORT = 8053  # Port 8053 for Global Ticker Library (8050=spymaster, 8051=impactsearch, 8052=onepass)
DASH_DEBUG = False

# Logging
LOG_LEVEL = "INFO"
ENABLE_ARTIFACTS = True  # Write additions.txt and removals.txt