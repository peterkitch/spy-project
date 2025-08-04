import yfinance as yf
import plotly.graph_objects as go
import dash
from dash import Dash, dcc, html, Input, Output, State, callback_context, no_update, dash_table
import dash_bootstrap_components as dbc
from dash.dependencies import Input, Output, State, ALL
import pandas as pd
from functools import lru_cache
from functools import partial
import pickle
from tqdm import tqdm
import os
os.environ['DASH_CALLBACK_TIMEOUT'] = '3000'  # Changed from 300 seconds (5 minutes) to 3000 seconds (50 minutes)
import json
import tempfile
import shutil
import time
import numpy as np
from scipy import stats
import gc
import threading
from threading import Lock
import signal
import atexit
import sys
from joblib import Memory
import logging
from tqdm.contrib.logging import logging_redirect_tqdm
import traceback
import random
import glob
from collections import defaultdict
import warnings
import pytz
from itertools import product
from dash.exceptions import PreventUpdate
from bs4 import BeautifulSoup
import uuid
import ast

# Initialize the Dash app with a dark theme and custom styles
app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY, "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css"])

# Add custom styles with spin animation
app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>PRJCT9 - Advanced Trading Analysis</title>
        {%favicon%}
        {%css%}
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@300;400;700&family=Share+Tech+Mono&family=Exo+2:wght@300;400;700;900&display=swap');
            
            /* Global font hierarchy */
            body {
                font-family: 'Rajdhani', monospace;
                font-weight: 400;
                letter-spacing: 0.5px;
            }
            
            h1 { 
                font-family: 'Orbitron', monospace !important;
                font-weight: 900 !important;
                text-transform: uppercase;
            }
            
            h2, h3, h4, h5 {
                font-family: 'Exo 2', sans-serif !important;
                font-weight: 700 !important;
                text-transform: uppercase;
                letter-spacing: 1.5px;
            }
            
            .btn {
                font-family: 'Share Tech Mono', monospace !important;
                text-transform: uppercase;
                letter-spacing: 1.2px;
                font-weight: 600;
            }
            
            input, .form-control {
                font-family: 'Share Tech Mono', monospace !important;
                letter-spacing: 0.8px;
            }
            
            .card-header {
                font-family: 'Exo 2', sans-serif !important;
                font-weight: 600;
                letter-spacing: 1px;
            }
            
            /* Table headers */
            th {
                font-family: 'Orbitron', monospace !important;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            
            /* Animations - slowed down by 50% */
            @keyframes spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
            
            @keyframes pulse-glow {
                0% { 
                    text-shadow: 0 0 20px rgba(128, 255, 0, 0.8);
                    filter: brightness(1);
                }
                50% { 
                    text-shadow: 0 0 40px rgba(128, 255, 0, 1), 0 0 60px rgba(128, 255, 0, 0.8);
                    filter: brightness(1.2);
                }
                100% { 
                    text-shadow: 0 0 20px rgba(128, 255, 0, 0.8);
                    filter: brightness(1);
                }
            }
            
            .pulsating-header {
                animation: pulse-glow 4s ease-in-out infinite;
            }
            .card {
                transition: all 0.3s ease;
                border: 1px solid rgba(128, 255, 0, 0.3);
            }
            .card:hover {
                transform: translateY(-2px);
                box-shadow: 0 5px 30px rgba(128, 255, 0, 0.5);
                border-color: rgba(128, 255, 0, 0.8);
            }
            .btn {
                transition: all 0.3s ease;
                position: relative;
                overflow: hidden;
            }
            .btn:hover {
                box-shadow: 0 0 20px rgba(128, 255, 0, 0.6);
                filter: brightness(1.1);
            }
            .btn::after {
                content: '';
                position: absolute;
                top: 50%;
                left: 50%;
                width: 0;
                height: 0;
                border-radius: 50%;
                background: rgba(255, 255, 255, 0.3);
                transform: translate(-50%, -50%);
                transition: width 0.6s, height 0.6s;
            }
            .btn:active::after {
                width: 300px;
                height: 300px;
            }
            input.form-control:focus {
                box-shadow: 0 0 10px rgba(128, 255, 0, 0.5);
                border-color: #80ff00;
            }
            .loading-text {
                animation: pulse 3s ease-in-out infinite;
            }
            @keyframes pulse {
                0% { opacity: 1; }
                50% { opacity: 0.5; }
                100% { opacity: 1; }
            }
            .processing-overlay {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0, 0, 0, 0.8);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 9999;
            }
            .processing-content {
                text-align: center;
                color: #80ff00;
            }
            .processing-spinner {
                border: 3px solid rgba(128, 255, 0, 0.3);
                border-top: 3px solid #80ff00;
                border-radius: 50%;
                width: 60px;
                height: 60px;
                animation: spin 1s linear infinite;
                margin: 0 auto 20px;
            }
            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(20px); }
                to { opacity: 1; transform: translateY(0); }
            }
            .fade-in {
                animation: fadeIn 0.5s ease-out;
            }
            .glow-border {
                animation: border-glow 4s ease-in-out infinite;
            }
            @keyframes border-glow {
                0% { box-shadow: 0 0 5px rgba(128, 255, 0, 0.5); }
                50% { box-shadow: 0 0 20px rgba(128, 255, 0, 0.8), 0 0 30px rgba(128, 255, 0, 0.6); }
                100% { box-shadow: 0 0 5px rgba(128, 255, 0, 0.5); }
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
'''

master_stopwatch_start = None

status_lock = threading.Lock()

# Remove any existing handlers
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

# Create a custom logger for your application
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Suppress various library logs
logging.getLogger('werkzeug').setLevel(logging.ERROR)  # HTTP requests
logging.getLogger('flask.app').setLevel(logging.ERROR)  # Flask logs
logging.getLogger('yfinance').setLevel(logging.ERROR)  # yfinance logs

# Color codes for terminal
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    # Custom colors for PROJECT 9
    NEON_GREEN = '\033[38;5;82m'
    BRIGHT_GREEN = '\033[38;5;46m'
    DIM_GREEN = '\033[38;5;22m'
    YELLOW = '\033[38;5;226m'
    ORANGE = '\033[38;5;208m'
    PURPLE = '\033[38;5;141m'
    CYAN = '\033[38;5;51m'

# Track which tickers have had their price data logged
_logged_price_tickers = set()

# Custom formatter with colors
class ColoredFormatter(logging.Formatter):
    format_dict = {
        logging.DEBUG: Colors.OKCYAN + '%(asctime)s - DEBUG - %(message)s' + Colors.ENDC,
        logging.INFO: Colors.OKGREEN + '%(message)s' + Colors.ENDC,
        logging.WARNING: Colors.WARNING + '[!] %(asctime)s - WARNING - %(message)s' + Colors.ENDC,
        logging.ERROR: Colors.FAIL + '[X] %(asctime)s - ERROR - %(message)s' + Colors.ENDC,
        logging.CRITICAL: Colors.FAIL + Colors.BOLD + '[!!!] %(asctime)s - CRITICAL - %(message)s' + Colors.ENDC,
    }
    
    def format(self, record):
        log_fmt = self.format_dict.get(record.levelno, '%(message)s')
        formatter = logging.Formatter(log_fmt, datefmt='%H:%M:%S')
        return formatter.format(record)

# Force UTF-8 encoding for Windows
import sys
import io
import os

# Set console to UTF-8 mode on Windows
if sys.platform == 'win32':
    # Set console code page to UTF-8
    os.system('chcp 65001 > nul 2>&1')

# Create console handler - let logging handle the encoding
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Force UTF-8 encoding on the handler's stream
if sys.platform == 'win32':
    # This is the key fix - set the encoding on the handler's stream
    console_handler.stream = open(sys.stdout.fileno(), 'w', encoding='utf-8', closefd=False)

file_handler = logging.FileHandler('debug.log', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)

# Create formatters and add them to handlers
console_handler.setFormatter(ColoredFormatter())

file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)

# Add handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Prevent logger from propagating messages to the root logger
logger.propagate = False

# Enhanced logging functions with colors
def log_separator(char="═", color=Colors.DIM_GREEN, width=80):
    logger.info(color + char * width + Colors.ENDC)

def log_section(section_name, color=Colors.NEON_GREEN):
    section_text = (
        color + "═" * 80 + Colors.ENDC + "\n" +
        color + Colors.BOLD + f"⚡ {section_name} ⚡".center(80, " ") + Colors.ENDC + "\n" +
        color + "═" * 80 + Colors.ENDC
    )
    logger.info(section_text)

def log_ticker_section(ticker, action="PROCESSING"):
    """Special section header for ticker changes"""
    logger.info("")  # Blank line before
    ticker_text = (
        Colors.PURPLE + "✦" * 80 + Colors.ENDC + "\n" +
        Colors.PURPLE + Colors.BOLD + f"📊 TICKER: {ticker} | {action} 📊".center(80, " ") + Colors.ENDC + "\n" +
        Colors.PURPLE + "✦" * 80 + Colors.ENDC
    )
    logger.info(ticker_text)

def log_success(message):
    logger.info(Colors.BRIGHT_GREEN + "[✓] " + message + Colors.ENDC)

def log_processing(message):
    logger.info(Colors.CYAN + "[⚙️] " + message + Colors.ENDC)

def log_result(label, value, color=Colors.YELLOW):
    # Ensure output fits within 80 chars
    formatted_line = f"{label}: {value}"
    if len(formatted_line) > 76:  # Leave room for prefix
        formatted_line = formatted_line[:73] + "..."
    logger.info(f"  {Colors.OKGREEN}{label}:{Colors.ENDC} {color}{Colors.BOLD}{value}{Colors.ENDC}")

def log_metric(label, value, unit="", indent=2):
    """Log a metric with consistent formatting"""
    indent_str = " " * indent
    if unit:
        logger.info(f"{indent_str}{Colors.CYAN}{label}:{Colors.ENDC} {Colors.YELLOW}{value}{unit}{Colors.ENDC}")
    else:
        logger.info(f"{indent_str}{Colors.CYAN}{label}:{Colors.ENDC} {Colors.YELLOW}{value}{Colors.ENDC}")

def log_data_info(label, value, color=Colors.BRIGHT_GREEN):
    """Log data information with consistent formatting"""
    logger.info(f"  {Colors.OKBLUE}{label}:{Colors.ENDC} {color}{value}{Colors.ENDC}")

def log_warning_msg(message):
    logger.info(Colors.WARNING + "[⚠️] " + message + Colors.ENDC)

def log_error_msg(message):
    logger.info(Colors.FAIL + "[❌] " + message + Colors.ENDC)

def log_subsection(title, char="─", color=Colors.DIM_GREEN):
    """Create a subsection with lighter separators"""
    logger.info("")
    logger.info(color + char * 40 + Colors.ENDC)
    logger.info(color + f"🔸 {title} 🔸".center(40, " ") + Colors.ENDC)
    logger.info(color + char * 40 + Colors.ENDC)

# Suppress yfinance debug logs
logging.getLogger('yfinance').setLevel(logging.WARNING)

# Suppress urllib3 debug logs
logging.getLogger('urllib3').setLevel(logging.WARNING)

tqdm.pandas()

# Configure TQDM to fit within 80 characters
from tqdm import tqdm as original_tqdm

# Create a wrapper class that preserves all tqdm functionality
class CustomTqdm(original_tqdm):
    def __init__(self, *args, **kwargs):
        # Set default parameters for width and ASCII
        kwargs.setdefault('ncols', 75)  # Reduced to ensure it fits
        kwargs.setdefault('ascii', True)
        kwargs.setdefault('leave', True)
        kwargs.setdefault('bar_format', '{l_bar}{bar}| {n_fmt}/{total_fmt}')  # Simplified format
        super().__init__(*args, **kwargs)
    
    @staticmethod
    def write(*args, **kwargs):
        # Preserve the write method
        original_tqdm.write(*args, **kwargs)

# Override tqdm with our custom version
tqdm = CustomTqdm

# ============================================================================
# CONFIGURATION AND GLOBAL VARIABLES
# ============================================================================
MAX_SMA_DAY = 114
_precomputed_results_cache = {}
_loading_in_progress = {}
_loading_lock = threading.Lock()

status_lock = Lock()

optimization_lock = threading.Lock()
optimization_in_progress = False
optimization_results_cache = {}  # Add this line to store results

# Set up persistent cache
cache_dir = '.cache'
os.makedirs(cache_dir, exist_ok=True)
memory = Memory(cache_dir, verbose=0)

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================
def normalize_ticker(ticker):
    """Normalize ticker to uppercase if it exists"""
    return ticker.strip().upper() if ticker else ticker

def fetch_data(ticker, is_secondary=False):
    try:
        # Check if we've already determined this is an invalid ticker
        status_file = f"{ticker}_status.json"
        if os.path.exists(status_file):
            try:
                with open(status_file, 'r') as f:
                    status = json.load(f)
                    if status.get('message') == "Invalid ticker symbol":
                        logger.warning(f"Skipping known invalid ticker: {ticker}")
                        return pd.DataFrame()
            except Exception as e:
                logger.error(f"Error reading status file for {ticker}: {str(e)}")

        # Check for empty or whitespace-only ticker
        if not ticker or not ticker.strip():
            if not is_secondary:
                logger.warning("No primary ticker provided")
            return pd.DataFrame()
            
        # Normalize ticker
        ticker = normalize_ticker(ticker)
        
        # Add retries for network issues
        max_retries = 3
        retry_delay = 2
        df = pd.DataFrame()
        for attempt in range(max_retries):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    # auto_adjust=False to ensure we get both Adj Close and Close columns
                    df = yf.download(ticker, period='max', interval='1d', progress=False, 
                                   auto_adjust=False)
                if not df.empty:
                    break
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed for {ticker}: {str(e)}. Retrying...")
                time.sleep(retry_delay)
        else:
            # If df is empty after all retries
            logger.error(f"No data fetched for {ticker}")
            write_status(ticker, {"status": "failed", "message": "No data"})
            return pd.DataFrame()

        # Ensure the index is datetime and timezone naive
        df.index = pd.to_datetime(df.index).tz_localize(None)
        
        # Track if we're using adjusted prices
        using_adjusted = False
        
        # Handle column names properly
        if isinstance(df.columns, pd.MultiIndex):
            try:
                # Try to get Adj Close first
                if ('Adj Close', ticker) in df.columns:
                    price_data = df['Adj Close'][ticker]
                    using_adjusted = True
                elif ('Adj Close', '') in df.columns:
                    price_data = df['Adj Close']['']
                    using_adjusted = True
                # Fall back to Close if Adj Close is not available
                elif ('Close', ticker) in df.columns:
                    price_data = df['Close'][ticker]
                elif ('Close', '') in df.columns:
                    price_data = df['Close']['']
                else:
                    logger.error(f"Could not find price data for {ticker} in MultiIndex columns")
                    return pd.DataFrame()
                df = pd.DataFrame({'Close': price_data}, index=df.index)
            except Exception as e:
                logger.error(f"Error processing MultiIndex data for {ticker}: {str(e)}")
                return pd.DataFrame()
        else:
            try:
                # For single-level columns, standardize names and prefer Adj Close
                df.columns = [str(col).capitalize() for col in df.columns]
                if 'Adj Close' in df.columns:
                    price_data = df['Adj Close']
                    using_adjusted = True
                elif 'Adj_Close' in df.columns:
                    price_data = df['Adj_Close']
                    using_adjusted = True
                elif 'Close' in df.columns:
                    price_data = df['Close']
                else:
                    logger.error(f"No price data found in single-level columns for {ticker}")
                    return pd.DataFrame()
                df = pd.DataFrame({'Close': price_data}, index=df.index)
            except Exception as e:
                logger.error(f"Error processing single-level data for {ticker}: {str(e)}")
                return pd.DataFrame()
                
        # Log price type only once per ticker
        global _logged_price_tickers
        if ticker not in _logged_price_tickers and not is_secondary:
            if using_adjusted:
                log_result("Price Data", f"Using Adjusted Close for {ticker}", Colors.BRIGHT_GREEN)
            else:
                logger.warning(f"Adjusted Close not available for {ticker} - defaulting to Close prices")
            _logged_price_tickers.add(ticker)
        
        if df.empty:
            logger.error(f"No valid data found for {ticker}")
            return pd.DataFrame()
        
        if not is_secondary:
            logging.info(f"Successfully fetched primary ticker {ticker} data ({len(df)} periods)")
            
            # Function to check if ticker is crypto
            def is_crypto_ticker(ticker_symbol):
                # Common crypto suffixes used by Yahoo Finance
                crypto_suffixes = ['-USD', '-USDT', '-BTC', '-ETH', '-CAD', '-JPY']
                # Check if ticker ends with any crypto suffix
                return any(ticker_symbol.endswith(suffix) for suffix in crypto_suffixes)
            
            # Check if we should add today's date
            today = pd.Timestamp.now().normalize().tz_localize(None)
            
            # Different handling for crypto vs traditional assets
            if is_crypto_ticker(ticker):
                logger.info(f"Crypto ticker {ticker} detected - allowing 24/7 trading")
                if len(df) > 0 and df.index[-1] < today:
                    last_adj_close = df['Close'].iloc[-1]  # Already using adjusted price if available
                    df.loc[today, 'Close'] = last_adj_close
                    logger.info(f"Added current day {today} to crypto data")
            else:
                # Only add today if:
                # 1. It's a weekday
                # 2. It's not a future date
                # 3. The last date in df is earlier than today
                # 4. The time is during market hours (9:30 AM - 4:00 PM ET)
                if (len(df) > 0 and 
                    df.index[-1] < today and 
                    today.weekday() < 5):  # 0-4 represents Monday-Friday
                    
                    # Convert to Eastern Time for market hours check
                    et_tz = pytz.timezone('US/Eastern')
                    et_now = pd.Timestamp.now(tz=et_tz)
                    market_open = et_now.replace(hour=9, minute=30)
                    market_close = et_now.replace(hour=16, minute=0)
                    
                    # Only add today's date if we're during market hours
                    if market_open <= et_now <= market_close:
                        last_adj_close = df['Close'].iloc[-1]  # Already using adjusted price if available
                        df.loc[today, 'Close'] = last_adj_close
                        logger.debug(f"Added current market day {today} to data")
                    else:
                        logger.debug("Current time is outside market hours, not adding today's date")
                else:
                    if today.weekday() >= 5:
                        logger.debug("Current day is weekend, not adding today's date")
                    elif df.index[-1] >= today:
                        logger.debug("Data already includes the latest date")
                    else:
                        logger.debug("Conditions not met for adding current date")      
        return df
    except Exception as e:
        logging.error(f"Failed to fetch data for '{ticker}': {type(e).__name__} - {str(e)}")
        return pd.DataFrame()

# ============================================================================
# DATA FETCHING AND PROCESSING FUNCTIONS
# ============================================================================
def get_last_valid_trading_day(df):
    """Get the most recent day with valid adjusted trading data."""
    for date in sorted(df.index, reverse=True):
        if pd.notna(df.loc[date, 'Close']):  # Already using adjusted price stored in 'Close'
            return date
    return None

def load_precomputed_results_from_file(pkl_file, max_retries=5, delay=1):
    retries = 0
    while retries < max_retries:
        try:
            with open(pkl_file, 'rb') as f:
                data = pickle.load(f)
            return data
        except PermissionError:
            logging.error(f"Permission denied when loading results from {pkl_file}. Retrying...")
            time.sleep(delay)
            retries += 1
        except FileNotFoundError:
            logging.warning(f"Results file not found: {pkl_file}")
            break
        except Exception as e:
            logging.error(f"Error loading results from {pkl_file}: {str(e)}")
            break
    logging.error(f"Failed to load results from {pkl_file} after {max_retries} retries.")
    return None

def load_precomputed_results(ticker, load_full_data=False):
    global _precomputed_results_cache, _loading_in_progress
    
    with _loading_lock:
        if ticker in _precomputed_results_cache:
            # Only log debug info for cached results to prevent duplicate headers
            logger.debug(f"Using cached results for {ticker.upper()}")
            return _precomputed_results_cache[ticker]

        if ticker in _loading_in_progress:
            logger.debug(f"Loading in progress for {ticker}")
            return None  # Return None immediately if loading is in progress

        # Log ticker input for new requests
        logger.info(f"{Colors.CYAN}[🔍] User entered ticker: {Colors.YELLOW}{ticker.upper()}{Colors.ENDC}")
        
        # Attempt to load from file if not in cache and not currently loading
        pkl_file = f'{ticker}_precomputed_results.pkl'
        if os.path.exists(pkl_file):
            log_ticker_section(ticker.upper(), "LOADING EXISTING DATA")
            log_processing(f"Loading precomputed results from file for {ticker.upper()}")
            load_start_time = time.time()
            results = load_precomputed_results_from_file(pkl_file)
            if results:
                if load_full_data:
                    # Load buy and short results incrementally
                    buy_results = {}
                    short_results = {}
                    chunk_files = sorted(glob.glob(f'{ticker}_results_chunk_*.npz'))
                    
                    chunk_load_start = time.time()

                    with tqdm(total=len(chunk_files), desc=f"Loading chunks", unit="chunk") as pbar:
                        for chunk_file in chunk_files:
                            data = np.load(chunk_file, allow_pickle=True)
                            buy_pairs = data['buy_pairs']
                            buy_values = data['buy_values']
                            short_pairs = data['short_pairs']
                            short_values = data['short_values']

                            # Reconstruct dictionaries
                            buy_results.update(zip(map(tuple, buy_pairs), buy_values))
                            short_results.update(zip(map(tuple, short_pairs), short_values))

                            pbar.update(1)
                    
                    results['buy_results'] = buy_results
                    results['short_results'] = short_results

                _precomputed_results_cache[ticker] = results
                logger.debug(f"Loaded results from file for {ticker.upper()}")
                return results
            else:
               logger.warning(f"Failed to load results from file for {ticker.upper()}")

        # Check if we've already tried and failed due to insufficient data
        status = read_status(ticker)
        if status.get('message') == "Insufficient trading history":
            return None

        log_ticker_section(ticker.upper(), "COMPUTING NEW DATA")
        log_processing(f"Starting to precompute results for {ticker.upper()}...")
        event = threading.Event()
        _loading_in_progress[ticker] = event
        # Set daemon=True so the thread doesn't prevent program exit
        thread = threading.Thread(target=precompute_results, args=(ticker, event))
        thread.daemon = True
        thread.start()
        return None

def fetch_precomputed_results(ticker):
    precomputed_results = load_precomputed_results(ticker)

    if precomputed_results:
        top_buy_pair = precomputed_results.get('top_buy_pair')
        top_short_pair = precomputed_results.get('top_short_pair')
        buy_results = precomputed_results.get('buy_results')
        short_results = precomputed_results.get('short_results')
    else:
        # Set default values if precomputed results are not available
        top_buy_pair = None
        top_short_pair = None
        buy_results = {}
        short_results = {}

    return top_buy_pair, top_short_pair, buy_results, short_results

def get_data(ticker, MAX_SMA_DAY):
    # Use logger instead of logging for consistency
    # Internal function call - no logging needed
    # Force flush to ensure output appears
    for handler in logger.handlers:
        handler.flush()
    
    results = load_precomputed_results(ticker)
    return results
    
def compute_signals(df, sma1, sma2):
    # Align the indexes of sma1 and sma2
    sma1, sma2 = sma1.align(sma2)

    # Calculate signals where the signal remains True as long as sma1 is greater than sma2
    signals = sma1 > sma2

    # Check if the 'Close' column exists in the DataFrame
    if 'Close' not in df.columns:
        raise KeyError("The 'Close' column is missing in the DataFrame.")

    # Calculate daily returns
    daily_returns = df['Close'].pct_change()

    # Calculate captures by applying the signal directly to the daily returns
    buy_returns = daily_returns.copy()
    buy_returns[~signals] = 0
    buy_capture = buy_returns.cumsum()

    short_returns = -daily_returns.copy()
    short_returns[signals] = 0
    short_capture = short_returns.cumsum()

    return {'buy_capture': buy_capture, 'short_capture': short_capture}

def write_status(ticker, status):
    ticker = normalize_ticker(ticker)
    status_file = f"{ticker}_status.json"
    with status_lock:
        with open(status_file, 'w') as f:
            json.dump(status, f)

def save_precomputed_results(ticker, results):
    ticker = normalize_ticker(ticker)
    final_name = f'{ticker}_precomputed_results.pkl'
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pkl') as tf:
        pickle.dump(results, tf)
        temp_name = tf.name

    shutil.move(temp_name, final_name)
    # Don't log here - it disrupts progress bar output
    return results

def process_chunk_for_top_pairs(chunk_file, total_days):
    data = np.load(chunk_file, allow_pickle=True)
    buy_pairs = data['buy_pairs']
    buy_values = data['buy_values']
    short_pairs = data['short_pairs']
    short_values = data['short_values']

    buy_values = np.array(buy_values)
    short_values = np.array(short_values)

    max_buy_captures = np.full(total_days, -np.inf)
    max_short_captures = np.full(total_days, -np.inf)
    max_buy_pairs = [None] * total_days
    max_short_pairs = [None] * total_days

    num_pairs = len(buy_pairs)
    for i in range(num_pairs):
        buy_capture = buy_values[i]
        short_capture = short_values[i]
        buy_pair = tuple(buy_pairs[i])
        short_pair = tuple(short_pairs[i])

        # Update buy captures
        better_buy = buy_capture > max_buy_captures
        max_buy_captures = np.where(better_buy, buy_capture, max_buy_captures)
        max_buy_pairs = [buy_pair if better_buy[j] else max_buy_pairs[j] for j in range(total_days)]

        # Update short captures
        better_short = short_capture > max_short_captures
        max_short_captures = np.where(better_short, short_capture, max_short_captures)
        max_short_pairs = [short_pair if better_short[j] else max_short_pairs[j] for j in range(total_days)]

    return max_buy_captures, max_buy_pairs, max_short_captures, max_short_pairs

# ============================================================================
# SIGNAL CALCULATION AND OPTIMIZATION FUNCTIONS
# ============================================================================
def calculate_daily_top_pairs(df, ticker):
    section_start = time.time()
    log_processing("Calculating daily top pairs...")
    total_days = len(df.index)
    dates = df.index

    # Get list of chunk files
    chunk_files = sorted(glob.glob(f'{ticker}_results_chunk_*.npz'))
    logger.info(f"Found {len(chunk_files)} chunk files to process.")

    if not chunk_files:
        logger.warning("No chunk files found. Cannot calculate daily top pairs.")
        return {date: ((1, 2), 0.0) for date in dates}, {date: ((1, 2), 0.0) for date in dates}, 0

    # Initialize arrays to store max captures and corresponding pairs
    max_buy_captures_global = np.full(total_days, -np.inf)
    max_short_captures_global = np.full(total_days, -np.inf)
    max_buy_pairs_global = [None] * total_days
    max_short_pairs_global = [None] * total_days

    chunk_processing_times = []
    with tqdm(total=len(chunk_files), desc="Processing top pairs", unit="chunk") as pbar:
        for chunk_file in chunk_files:
            tqdm.write(f"Processing chunk file: {chunk_file}")
            chunk_start_time = time.time()
            try:
                max_buy_captures, max_buy_pairs, max_short_captures, max_short_pairs = process_chunk_for_top_pairs(chunk_file, total_days)
                tqdm.write(f"Finished processing {chunk_file}")

                # Update global max captures and pairs
                better_buy = max_buy_captures > max_buy_captures_global
                max_buy_captures_global = np.where(better_buy, max_buy_captures, max_buy_captures_global)
                max_buy_pairs_global = [max_buy_pairs[i] if better_buy[i] else max_buy_pairs_global[i] for i in range(total_days)]

                better_short = max_short_captures > max_short_captures_global
                max_short_captures_global = np.where(better_short, max_short_captures, max_short_captures_global)
                max_short_pairs_global = [max_short_pairs[i] if better_short[i] else max_short_pairs_global[i] for i in range(total_days)]

            except Exception as exc:
                logger.error(f'Chunk {chunk_file} generated an exception: {exc}')
            chunk_end_time = time.time()
            chunk_processing_times.append(chunk_end_time - chunk_start_time)
            pbar.update(1)

    # Build the daily top pairs dictionaries
    daily_top_buy_pairs = {}
    daily_top_short_pairs = {}
    for i, date in enumerate(dates):
        daily_top_buy_pairs[date] = (max_buy_pairs_global[i], float(max_buy_captures_global[i]))
        daily_top_short_pairs[date] = (max_short_pairs_global[i], float(max_short_captures_global[i]))

    logger.info(f"Number of daily top pairs: Buy: {len(daily_top_buy_pairs)}, Short: {len(daily_top_short_pairs)}")
    logger.info("Daily top pairs calculation completed.")

    total_chunk_processing_time = sum(chunk_processing_times)
    logger.info(f"Total time for processing chunks: {total_chunk_processing_time:.2f} seconds")

    section_time = time.time() - section_start
    logger.info(f"Total time for Daily Top Pairs Calculation: {section_time:.2f} seconds")

    return daily_top_buy_pairs, daily_top_short_pairs, total_chunk_processing_time

def calculate_captures_vectorized(sma1, sma2, returns):
    """Calculate captures using adjusted price returns."""
    try:
        # Ensure inputs are numpy arrays (returns are based on adjusted prices)
        sma1 = np.asarray(sma1)
        sma2 = np.asarray(sma2)
        returns = np.asarray(returns)
        
        # Calculate signals with proper handling of NaN values
        buy_signals = np.logical_and(np.isfinite(sma1), np.isfinite(sma2))
        buy_signals = np.logical_and(buy_signals, (sma1 > sma2))
        
        short_signals = np.logical_and(np.isfinite(sma1), np.isfinite(sma2))
        short_signals = np.logical_and(short_signals, (sma1 < sma2))

        # Shift signals to align with returns
        buy_signals_shifted = np.roll(buy_signals, 1, axis=1)
        short_signals_shifted = np.roll(short_signals, 1, axis=1)

        # Replace NaN signals with False
        buy_signals_shifted[:, 0] = False
        short_signals_shifted[:, 0] = False

        # Calculate captures
        buy_returns = buy_signals_shifted * returns
        short_returns = short_signals_shifted * -returns

        # Use cumulative sum
        buy_capture = np.nancumsum(buy_returns, axis=1)
        short_capture = np.nancumsum(short_returns, axis=1)

        return buy_capture, short_capture
        
    except Exception as e:
        logger.error(f"Error in calculate_captures_vectorized: {str(e)}")
        return np.zeros_like(returns), np.zeros_like(returns)

def save_precomputed_results_chunk(ticker, buy_results_chunk, short_results_chunk, chunk_index):
    ticker = normalize_ticker(ticker)
    chunk_file = f'{ticker}_results_chunk_{chunk_index}.npz'
    try:
        # Validate input data types
        if not isinstance(buy_results_chunk, dict) or not isinstance(short_results_chunk, dict):
            logger.error(f"Invalid chunk data types for {ticker}: expected dicts but got {type(buy_results_chunk)} and {type(short_results_chunk)}")
            np.savez(chunk_file, buy_pairs=np.array([]), buy_values=np.array([]),
                     short_pairs=np.array([]), short_values=np.array([]))
            return

        # Attempt to extract a sample array of values to determine the number of days
        combined_dict = {**buy_results_chunk, **short_results_chunk}
        example_values = next(iter(combined_dict.values()), None)
        
        # Check if we have valid data
        if example_values is None or not isinstance(example_values, np.ndarray):
            logger.info(f"No valid arrays found in chunk {chunk_index} for {ticker}. Saving empty results.")
            np.savez(chunk_file, buy_pairs=np.array([]), buy_values=np.array([]),
                     short_pairs=np.array([]), short_values=np.array([]))
            return

        num_days = len(example_values)

        # Convert dictionaries to numpy arrays for vectorized operations
        buy_pairs = np.array(list(buy_results_chunk.keys()), dtype=object)
        buy_values = np.array(list(buy_results_chunk.values()), dtype=np.float64)
        short_pairs = np.array(list(short_results_chunk.keys()), dtype=object)
        short_values = np.array(list(short_results_chunk.values()), dtype=np.float64)

        # Handle the case where we might have no data (empty arrays)
        if buy_values.size == 0:
            top_buy_pairs = np.array([], dtype=object)
            top_buy_values = np.array([], dtype=np.float64)
        else:
            # Use nanargmax to handle any potential NaN values
            max_buy_indices = np.nanargmax(buy_values, axis=0)
            top_buy_pairs = buy_pairs[max_buy_indices]
            top_buy_values = buy_values[max_buy_indices, np.arange(num_days)]

        if short_values.size == 0:
            top_short_pairs = np.array([], dtype=object)
            top_short_values = np.array([], dtype=np.float64)
        else:
            max_short_indices = np.nanargmax(short_values, axis=0)
            top_short_pairs = short_pairs[max_short_indices]
            top_short_values = short_values[max_short_indices, np.arange(num_days)]

        # Save the results in compressed format to reduce file size
        np.savez_compressed(chunk_file,
                            buy_pairs=top_buy_pairs, buy_values=top_buy_values,
                            short_pairs=top_short_pairs, short_values=top_short_values)

        file_size = os.path.getsize(chunk_file) / (1024 * 1024 * 1024)
        logger.info(f"Chunk {chunk_index} saved successfully for {ticker}: {file_size:.2f} GB")

    except Exception as e:
        logger.error(f"Error saving chunk {chunk_index} for {ticker}: {str(e)}")
        logger.error(traceback.format_exc())

# ============================================================================
# PRECOMPUTATION AND CACHING FUNCTIONS
# ============================================================================
def precompute_results(ticker, event):
    global master_stopwatch_start
    master_stopwatch_start = time.time()
    section_times = {}
    global _loading_in_progress, _precomputed_results_cache
    
    # Header is shown by log_ticker_section in load_precomputed_results
    with logging_redirect_tqdm():
        try:
            # Internal function call - no logging needed
            # Force flush to ensure output appears
            for handler in logger.handlers:
                handler.flush()
            section_start = time.time()
            
            def log_section_time(section_name):
                section_time = time.time() - section_start
                section_times[section_name] = section_time
                return time.time()
            
            df = fetch_data(ticker)
            if df is None or df.empty:
                write_status(ticker, {"status": "failed", "message": "No data"})
                logger.warning(f"No data fetched for {ticker}")
                return None
                
            # Check for minimum required trading days
            if len(df) < 2:  # Minimum 2 days needed for calculations
                write_status(ticker, {"status": "failed", "message": "Insufficient trading history"})
                logger.warning(f"Unable to process {ticker.upper()}: Found only {len(df)} trading day(s). Min. 2 trading days required.")
                logger.warning("Please enter a different ticker symbol.")
                return None
            
            logger.info("")  # Line break before section
            log_section("Data Preprocessing")
            log_processing(f"Data loading initiated for {ticker.upper()}")
            section_times['Data Preprocessing'] = time.time() - section_start
            section_start = time.time()

            pkl_file = f'{ticker}_precomputed_results.pkl'
            
            if os.path.exists(pkl_file):
                existing_results = load_precomputed_results_from_file(pkl_file)
                existing_max_sma_day = existing_results.get('existing_max_sma_day', 0)
                last_processed_date = existing_results.get('last_processed_date')
            else:
                existing_results = {}
                existing_max_sma_day = 0
                last_processed_date = None

            MAX_TRADING_DAYS = 30000  # Adjust if needed
            total_trading_days = len(df)
            if total_trading_days > MAX_TRADING_DAYS:
                df = df.iloc[-MAX_TRADING_DAYS:]
                logger.warning(f"Trimmed data to last {MAX_TRADING_DAYS} trading days due to memory constraints.")
            
            max_sma_day = min(MAX_SMA_DAY, len(df))
            needs_precompute = max_sma_day > existing_max_sma_day or last_processed_date != df.index[-1]
            
            logger.info(f"Total trading days: {total_trading_days}")
            logger.info(f"MAX_SMA_DAY: {max_sma_day}, existing_max_sma_day: {existing_max_sma_day}")
            logger.info(f"Needs precompute: {needs_precompute}")
            
            if not needs_precompute:
                logger.info(f"Existing results found for {ticker} and no precomputation needed. Using existing results.")
                results = load_precomputed_results(ticker)
                if 'active_pairs' not in results or not results['active_pairs']:
                    logger.info(f"'active_pairs' not found or empty for {ticker}, recalculating...")
                    daily_top_buy_pairs = results.get('daily_top_buy_pairs')
                    daily_top_short_pairs = results.get('daily_top_short_pairs')
                    if daily_top_buy_pairs and daily_top_short_pairs:
                        df = results['preprocessed_data']
                        cumulative_combined_captures, active_pairs = calculate_cumulative_combined_capture(df, daily_top_buy_pairs, daily_top_short_pairs)
                        results['cumulative_combined_captures'] = cumulative_combined_captures
                        results['active_pairs'] = active_pairs
                        save_precomputed_results(ticker, results)
                    else:
                        logger.warning(f"Missing daily top pairs for {ticker}, unable to recalculate 'active_pairs'.")

                # Ensure required keys exist
                if 'top_buy_pair' not in results:
                    results['top_buy_pair'] = (0,0)
                if 'top_short_pair' not in results:
                    results['top_short_pair'] = (0,0)
                if 'cumulative_combined_captures' not in results:
                    results['cumulative_combined_captures'] = pd.Series([0], index=[df.index[0]])
                if 'active_pairs' not in results:
                    results['active_pairs'] = ['None'] * len(df)

                write_status(ticker, {"status": "complete", "progress": 100})
                with _loading_lock:
                    _precomputed_results_cache[ticker] = results
                    if ticker in _loading_in_progress:
                        _loading_in_progress[ticker].set()
                        del _loading_in_progress[ticker]

                logger.info("Updating Dash app layout...")
                app.layout = app.layout
                logger.info("Dash app layout updated.")

                results['section_times'] = section_times
                results['start_time'] = master_stopwatch_start

                logger.info("Computation and loading process completed.")
                return results

            else:
                results = existing_results or {}

                start_date = df.index.min().strftime('%Y-%m-%d')
                last_date = df.index.max().strftime('%Y-%m-%d')
                logger.info(f"Date range: {start_date} to {last_date}")

            logger.info("")  # Line break before section
            log_section("SMA Calculation")
            logger.info("Checking SMA cache...")

            cache_dir = '.sma_cache'
            os.makedirs(cache_dir, exist_ok=True)
            sma_cache_path = os.path.join(cache_dir, f'sma_full_{ticker}.npz')

            smas_loaded = False
            if os.path.exists(sma_cache_path):
                logger.info("Loading SMAs from cache...")
                try:
                    with np.load(sma_cache_path) as data:
                        for i in range(1, max_sma_day + 1):
                            df[f'SMA_{i}'] = data[f'SMA_{i}']
                    logger.info("Successfully loaded SMAs from cache")
                    smas_loaded = True
                except Exception as e:
                    logger.warning(f"Error loading SMAs from cache: {str(e)}")

            if not smas_loaded:
                logger.info("Computing new SMA columns...")
                if max_sma_day > existing_max_sma_day:
                    sma_list = []
                    logger.info("Beginning SMA calculations in chunks...")
                    chunk_size_sma = 50
                    total_chunks = (max_sma_day - existing_max_sma_day + chunk_size_sma - 1) // chunk_size_sma

                    with tqdm(total=total_chunks, desc="Processing SMA chunks", unit="chunk") as pbar:
                        for i in range(existing_max_sma_day + 1, max_sma_day + 1, chunk_size_sma):
                            chunk_end = min(i + chunk_size_sma, max_sma_day + 1)
                            sma_dict = {}
                            for j in range(i, chunk_end):
                                sma_values = df['Close'].rolling(window=j, min_periods=j).mean().squeeze()
                                sma_dict[f'SMA_{j}'] = sma_values
                            sma_chunk = pd.DataFrame(sma_dict, index=df.index)
                            sma_list.append(sma_chunk)
                            gc.collect()
                            pbar.update(1)

                    logger.info(f"\nCompleted SMA calculations for {max_sma_day - existing_max_sma_day} new periods")

                    sma_df = pd.concat(sma_list, axis=1)
                    df = pd.concat([df, sma_df], axis=1)
                    df = df.copy()
                    logger.info(f"Added {max_sma_day - existing_max_sma_day} new SMA columns to DataFrame.")

                else:
                    logger.info("No new SMA periods to compute.")
                    logger.info("Updating existing SMA columns for new data.")
                    for sma_period in range(1, max_sma_day + 1):
                        sma_column_name = f'SMA_{sma_period}'
                        df[sma_column_name] = df['Close'].rolling(window=sma_period, min_periods=sma_period).mean()
                        df.iloc[:sma_period-1, df.columns.get_loc(sma_column_name)] = np.nan
                    df = df.copy()

                    logger.info("SMA columns updated.")
                    logger.info("Ensuring correct NaN values for SMA calculations.")
                    for j in range(1, max_sma_day + 1):
                        sma_column_name = f'SMA_{j}'
                        df.iloc[:j-1, df.columns.get_loc(sma_column_name)] = np.nan
                    logger.info("Ensured correct NaN values for SMA calculations.")

                    expected_sma_columns = [f'SMA_{i}' for i in range(1, max_sma_day + 1)]
                    missing_smas = [sma for sma in expected_sma_columns if sma not in df.columns]
                    if not missing_smas:
                        try:
                            sma_dict = {f'SMA_{i}': df[f'SMA_{i}'].values for i in range(1, max_sma_day + 1)}
                            np.savez_compressed(sma_cache_path, **sma_dict)
                            logger.info("Saved SMAs to cache after full computation")
                        except Exception as e:
                            logger.warning(f"Failed to save SMA cache: {str(e)}")
                    else:
                        logger.warning(f"Missing SMA columns even after computation: {missing_smas}. Cannot cache incomplete SMA data.")

            # Process SMA pairs and find top performers in a fully vectorized manner with chunking
            logger.info("")  # Line break before section
            log_section("SMA Pairs Processing")
            daily_top_buy_pairs = {}
            daily_top_short_pairs = {}

            dates = df.index
            returns = df['Close'].pct_change().fillna(0).values

            # Determine total pairs
            total_pairs = sum(1 for i in range(1, max_sma_day+1) for j in range(1, max_sma_day+1) if i != j)
            chunk_size_pairs = 100000 if max_sma_day <= 500 else 75000 if max_sma_day <= 1000 else 50000 if max_sma_day <= 1500 else 25000
            num_pair_chunks = (total_pairs + chunk_size_pairs - 1) // chunk_size_pairs

            logger.info(f"Processing {total_pairs} pairs in {num_pair_chunks} chunks of {chunk_size_pairs}")

            sma_matrix = np.empty((len(dates), max_sma_day), dtype=np.float64)
            for k in range(1, max_sma_day+1):
                sma_matrix[:, k-1] = df[f'SMA_{k}'].values

            pair_count = 0
            with tqdm(total=num_pair_chunks, desc="Processing SMA pair chunks", unit="chunk") as pbar_pairs:
                for chunk_idx in range(num_pair_chunks):
                    start_idx = chunk_idx * chunk_size_pairs
                    end_idx = min((chunk_idx + 1) * chunk_size_pairs, total_pairs)

                    # Generate pairs for this chunk
                    chunk_pairs = []
                    pc = 0
                    for i in range(1, max_sma_day+1):
                        for j in range(1, max_sma_day+1):
                            if i != j:
                                if pc >= start_idx and pc < end_idx:
                                    chunk_pairs.append((i, j))
                                pc += 1
                                if pc >= end_idx:
                                    break
                        if pc >= end_idx:
                            break

                    chunk_pairs = np.array(chunk_pairs)
                    num_pairs_chunk = len(chunk_pairs)
                    if num_pairs_chunk == 0:
                        pbar_pairs.update(1)
                        continue

                    i_indices = chunk_pairs[:, 0] - 1
                    j_indices = chunk_pairs[:, 1] - 1

                    sma_i = sma_matrix[:, i_indices]
                    sma_j = sma_matrix[:, j_indices]
                    buy_signals = np.vstack([np.zeros((1, num_pairs_chunk), dtype=bool), (sma_i[:-1] > sma_j[:-1])])
                    short_signals = np.vstack([np.zeros((1, num_pairs_chunk), dtype=bool), (sma_i[:-1] < sma_j[:-1])])

                    returns_expanded = returns[:, np.newaxis]
                    buy_captures = np.cumsum(returns_expanded * buy_signals * 100, axis=0)
                    short_captures = np.cumsum(-returns_expanded * short_signals * 100, axis=0)

                    # Update daily_top_buy_pairs and daily_top_short_pairs directly from this chunk
                    for day_idx in range(len(dates)):
                        # Buy
                        max_buy_val = np.max(buy_captures[day_idx])
                        # Reverse priority in case of ties
                        max_buy_idx = len(buy_captures[day_idx]) - 1 - np.argmax(buy_captures[day_idx][::-1])
                        current_buy_pair = tuple(chunk_pairs[max_buy_idx])
                        if dates[day_idx] not in daily_top_buy_pairs or max_buy_val > daily_top_buy_pairs[dates[day_idx]][1]:
                            daily_top_buy_pairs[dates[day_idx]] = (current_buy_pair, float(max_buy_val))

                        # Short
                        max_short_val = np.max(short_captures[day_idx])
                        # Reverse priority in case of ties
                        max_short_idx = len(short_captures[day_idx]) - 1 - np.argmax(short_captures[day_idx][::-1])
                        current_short_pair = tuple(chunk_pairs[max_short_idx])
                        if dates[day_idx] not in daily_top_short_pairs or max_short_val > daily_top_short_pairs[dates[day_idx]][1]:
                            daily_top_short_pairs[dates[day_idx]] = (current_short_pair, float(max_short_val))

                    del sma_i, sma_j, buy_signals, short_signals, buy_captures, short_captures
                    gc.collect()

                    pbar_pairs.update(1)
            
            # Add line break after progress bar
            logger.info("")
            
            # Update results
            results['daily_top_buy_pairs'] = daily_top_buy_pairs
            results['daily_top_short_pairs'] = daily_top_short_pairs

            write_status(ticker, {"status": "processing", "progress": 50})

            # Update other results
            results['preprocessed_data'] = df
            results['existing_max_sma_day'] = max_sma_day
            results['last_processed_date'] = df.index[-1]
            results['start_date'] = start_date
            results['last_date'] = last_date
            results['total_trading_days'] = total_trading_days

            # Begin Cumulative Combined Captures Calculation
            logger.info("")  # Line break before section
            log_section("Cumulative Combined Captures")
            section_start = log_section_time("Cumulative Combined Captures")
            cumulative_combined_captures, active_pairs = calculate_cumulative_combined_capture(
                df,
                results['daily_top_buy_pairs'],
                results['daily_top_short_pairs']
            )

            results['cumulative_combined_captures'] = cumulative_combined_captures
            results['active_pairs'] = active_pairs

            # Find best overall pairs from daily results
            last_day = df.index[-1]
            if last_day in results['daily_top_buy_pairs']:
                top_buy_pair = results['daily_top_buy_pairs'][last_day][0]
                top_buy_capture = results['daily_top_buy_pairs'][last_day][1]
            else:
                top_buy_pair = (0,0)
                top_buy_capture = 0

            if last_day in results['daily_top_short_pairs']:
                top_short_pair = results['daily_top_short_pairs'][last_day][0]
                top_short_capture = results['daily_top_short_pairs'][last_day][1]
            else:
                top_short_pair = (0,0)
                top_short_capture = 0

            results['top_buy_pair'] = top_buy_pair
            results['top_buy_capture'] = top_buy_capture
            results['top_short_pair'] = top_short_pair
            results['top_short_capture'] = top_short_capture

            # Ensure required keys are always present
            if 'cumulative_combined_captures' not in results:
                results['cumulative_combined_captures'] = pd.Series([0], index=[df.index[0]])
            if 'active_pairs' not in results:
                results['active_pairs'] = ['None'] * len(df)
            if 'top_buy_pair' not in results:
                results['top_buy_pair'] = (0,0)
            if 'top_short_pair' not in results:
                results['top_short_pair'] = (0,0)

            logger.info(f"Current Top Buy Pair for {ticker.upper()}: {top_buy_pair} with total capture {top_buy_capture}")
            logger.info(f"Current Top Short Pair for {ticker.upper()}: {top_short_pair} with total capture {top_short_capture}")

            logger.info(f"Saving final results to {pkl_file}")
            with tqdm(total=1, desc="Saving final results", unit="file", leave=True, position=0) as pbar_save:
                save_precomputed_results(ticker, results)

                pbar_save.update(1)
            
            # Add line break after progress bar
            logger.info("")
            
            write_status(ticker, {"status": "complete", "progress": 100})
            
            log_success("Process completed.")

            with _loading_lock:
                _precomputed_results_cache[ticker] = results
                if ticker in _loading_in_progress:
                    _loading_in_progress[ticker].set()
                    del _loading_in_progress[ticker]

            logger.info("Updating Dash app layout...")
            app.layout = app.layout
            logger.info("Dash app layout updated.")

            results['section_times'] = section_times
            results['start_time'] = master_stopwatch_start

        except Exception as e:
            logger.error(f"Error in precompute_results for {ticker}: {str(e)}")
            logger.error(traceback.format_exc())
        finally:
            with _loading_lock:
                if ticker in _loading_in_progress:
                    _loading_in_progress[ticker].set()
                    del _loading_in_progress[ticker]

    logger.info("Computation and loading process completed.")

def print_timing_summary(ticker):
    results = _precomputed_results_cache.get(ticker)
    if results and 'section_times' in results and 'start_time' in results:
        section_times = results['section_times']
        start_time = results['start_time']
        
        total_time = time.time() - start_time
        hours, rem = divmod(total_time, 3600)
        minutes, seconds = divmod(rem, 60)
        
        logger.info("")  # Line break before section
        log_section("PROCESSING TIME SUMMARY", Colors.CYAN)
        
        for section, time_taken in section_times.items():
            log_metric(section, f"{time_taken:.2f}", " seconds")
        
        if 'chunk_processing_time' in results:
            log_metric("Daily Top Pairs Chunk Processing", f"{results['chunk_processing_time']:.2f}", " seconds")
        
        logger.info("")
        log_separator("-", Colors.DIM_GREEN)
        log_result("Total processing time", f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d} (hh:mm:ss)")
        log_separator("═", Colors.DIM_GREEN)
        logger.info("Load complete. Data is now available in the Dash app.")
    elif results and 'load_time' in results:
        load_time = results['load_time']
        hours, rem = divmod(load_time, 3600)
        minutes, seconds = divmod(rem, 60)
        log_separator("═", Colors.DIM_GREEN)
        logger.info(f"Loading time for existing {ticker.upper()} data: {int(hours):02d}:{int(minutes):02d}:{int(seconds):02d} (hh:mm:ss)")
        log_separator("═", Colors.DIM_GREEN)
        logger.info("Load complete. Data is now available in the Dash app.")

# Function to read the processing status from a file
def read_status(ticker):
    ticker = normalize_ticker(ticker)
    status_path = f"{ticker}_status.json"
    with status_lock:
        if os.path.exists(status_path):
            with open(status_path, 'r') as file:
                try:
                    return json.load(file)
                except json.JSONDecodeError:
                    print(f"Empty JSON file: {status_path}")
        return {"status": "not started", "progress": 0}

def inspect_pkl_file(ticker, sample_size=5):
    pkl_file = f'{ticker}_precomputed_results.pkl'
    if os.path.exists(pkl_file):
        with open(pkl_file, 'rb') as f:
            results = pickle.load(f)
        keys = list(results.keys())
        sample_keys = random.sample(keys, min(sample_size, len(keys)))
        logger.info(f"Sample keys in {pkl_file}: {sample_keys}")
    else:
        logger.warning(f"{pkl_file} does not exist.")

# ============================================================================
# APPLICATION LAYOUT
# ============================================================================
app.layout = dbc.Container(
    fluid=True,
    style={
        'background-color': 'black',
        'color': '#80ff00',
        'font-family': 'Impact, sans-serif',
        'padding': '20px 40px',
        'minHeight': '100vh'
    },
    children=[
        # Header of the app
        html.Div([
            html.H1([
                html.Span('PR', style={'display': 'inline'}),
                html.Span(
                    html.I(className="fas fa-atom", 
                          style={
                              "color": "#80ff00", 
                              "animation": "spin 8s linear infinite",
                              "fontSize": "0.85em"
                          }),
                    style={
                        "display": "inline-block",
                        "verticalAlign": "middle",
                        "margin": "0 2px",  # Equal spacing on both sides
                        "position": "relative",
                        "left": "-3px",  # Shift left by 3px
                        "width": "1em",  # Fixed width container
                        "height": "1em",  # Fixed height container
                        "lineHeight": "1em",
                        "textAlign": "center"
                    }
                ),
                html.Span('JCT9', style={'display': 'inline'})
            ], 
            className='text-center mt-3 pulsating-header',
            style={
                "fontSize": "60px",
                "letterSpacing": "8px",
                "fontFamily": "Orbitron, monospace",
                "fontWeight": "900",
                "display": "flex",
                "alignItems": "center",
                "justifyContent": "center"
            }),
            html.P(
                'Adaptive Simple Moving Average Pair Optimization and Mean Reversion-Based Systematic Trading Framework',
                className='text-center',
                style={
                    "color": "#80ff00",
                    "fontSize": "14px",
                    "marginTop": "10px",
                    "fontFamily": "Rajdhani, monospace",
                    "letterSpacing": "2px",
                    "opacity": "0.8"
                }
            ),
        ]),
        # Help button and modal for step-by-step guidance (using dbc.Modal)
        dbc.Button(
            [html.I(className="fas fa-question-circle me-2 pulse", style={"animation": "pulse 4s infinite"}), "Help"], 
            id="help-button", 
            color="success", 
            className="mb-3",
            style={
                "boxShadow": "0 0 15px rgba(128, 255, 0, 0.6)",
                "position": "fixed",
                "top": "20px",
                "right": "20px",
                "zIndex": "1000",
                "fontWeight": "bold",
                "letterSpacing": "1px"
            }
        ),
        dbc.Modal(
            [
                dbc.ModalHeader("Understanding the Trading App - A Fisherman's Guide to Signal Mining"),
                dbc.ModalBody(
                    [
                        html.P("This app is an exploratory tool for discovering statistical relationships between securities (and themselves) using adaptive moving average signals."),
                        html.H5("Getting Started:"),
                        html.P([
                            "1. Begin with impactsearch.py ",
                            html.A("http://127.0.0.1:8051/", href="http://127.0.0.1:8051/", target="_blank"),
                            " to identify potential statistical relationships between tickers (You'll have to make sure the script is running in a separate console). Ensure that the Secondary Ticker you enter is the ticker that you are investigating. All of the Primary Tickers that you enter will have the results of their trading signals being sent to the Secondary Ticker. You can enter as many Primary Tickers as you want. The more the better to find the strongest connections and predictible impact on your Secondary Ticker. When the processing is complete, you will find a .xlsx file in the project folder for you to sort through. Take a list of approximately 5 of the most positive and 5 of the most negative impacts on your Secondary Ticker with you to the next step."
                        ]),
                        html.H5("2. Core Workflow Example (S&P 500 Study):"),
                        html.Ul([
                            html.Li("Use the Ticker Batch Process to input your list of tickers from the impactsearch.py results."),
                            html.Li("In the Automated Signal Optimization Ssection, enter your same list of tickers (if you don't use the Ticker Batch Process first, you'll run into some annoying issues). Then enter your target Secondary Ticker in the Enter Secondary Ticker input field. Hit Optimize Signals and wait for the process to complete."),
                            html.Li("Look for combinations showing strong statistical significance. Use the carrots to sort the columns and find the best combinations. Note that you can only sort all available rows when you are on the first page of the results -- sorting results on any other page simply sorts the results of that given page."),
                            html.Li("Find a build that you would like to investigate and verify by clicking the cell containing the ticker combination. This will auto-populate the Multi-Primary Signal Aggregator section above."),
                            html.Li("From here you have multiple options. You can enter your secondary ticker in the secondary ticker field but you can also enter multiple secondary tickers. If you are curious about your build and it's impact on a wide range of tickers, input your universe of tickers (e.g., 1,500+ symbols) in the Secondary field."),
                            html.Li("Process time: ~12 minutes for 1,500 tickers."),
                            html.Li("Mine the results for high/low Sharpe Ratios and examine the metrics. Trading decisions should be based on statistical significance and not just the highest Sharpe Ratio. At a minimum, a 90 percent significance should be observed (up for debate)"),
                        ]),
                        html.H5("3. Critical Metrics Assessment:"),
                        html.Ul([
                            html.Li("Trigger Days: Below 30 typically indicates insufficient sample size."),
                            html.Li("Win/Loss Ratio: Consider in context with trigger day count."),
                            html.Li("Sharpe Ratio: Key performance indicator, but verify sample size."),
                            html.Li("Statistical Significance: Check p-values and confidence levels."),
                            html.Li("Total Capture: Evaluate alongside other metrics.")
                        ]),
                        html.H5("Key Principles:"),
                        html.Ul([
                            html.Li("Trade on Statistics, Not Emotion."),
                            html.Li("Question Results That Seem Too Good."),
                            html.Li("Verify Findings Using Multiple App Features."),
                            html.Li("Look for Unexpected Connections."),
                            html.Li("Maintain Healthy Skepticism."),
                            html.Li("Sample Size Matters.")
                        ]),
                        html.P("Remember: This is a data mining tool. The signals adapt daily, and no single 'build' or combination will work forever. Focus on finding statistical edges rather than perfect predictions."),
                        html.P("Warning: All analysis is based on historical data and statistical relationships. Past performance does not guarantee future results. Always verify signals across multiple timeframes and consider your risk tolerance.")
                    ]
                ),
                dbc.ModalFooter(
                    dbc.Button("Close", id="close-help", className="ml-auto")
                )
            ],
            id="help-modal",
            is_open=False
        ),
        # Display for maximum SMA day information
        html.Div(id='max-sma-day-display', style={'font-size': '18px', 'margin-bottom': '20px'}),
        # Row for Primary Ticker input
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.I(className="fas fa-search me-2", style={"color": "#00ff41"}),
                        'Select Primary Ticker Symbol (Signal Generator)'
                    ], className="glow-border"),
                    dbc.CardBody([
                        dbc.Input(
                            id='ticker-input', 
                            placeholder='Enter a valid ticker symbol (e.g., AAPL)', 
                            type='text',
                            debounce=True,
                            valid=False,
                            invalid=False
                        ),
                        dbc.Tooltip(
                            "Enter the ticker symbol for the asset you wish to analyze. This symbol will be used to fetch historical data and generate SMA-based signals.",
                            target="ticker-input",
                            placement="right"
                        ),
                        dbc.FormFeedback(
                            id='ticker-input-feedback',
                            type="invalid",
                            style={'color': '#ff0000', 'font-weight': 'normal'}
                        ),
                    ])
                ], className='mb-3')
            ], width=12)
        ]),
        # Store for timing summary flag
        dcc.Store(id='timing-summary-printed', data=False),
        # Row for Combined Capture Chart
        dbc.Row([
            dbc.Col([
                dcc.Loading(
                    id="loading-combined-capture",
                    type="circle",
                    color="#80ff00",
                    children=[
                        dcc.Graph(
                            id='combined-capture-chart',
                            figure=go.Figure(
                                layout=go.Layout(
                                    title=dict(text="Cumulative Combined Capture Chart", font=dict(color='#80ff00')),
                                    plot_bgcolor='black',
                                    paper_bgcolor='black',
                                    font=dict(color='#80ff00'),
                                    xaxis=dict(visible=False),
                                    yaxis=dict(visible=False),
                                    template='plotly_dark'
                                )
                            )
                        )
                    ]
                )
            ], width=12)
        ]),
        # Row for Historical Top Pairs Chart
        dbc.Row([
            dbc.Col([
                dcc.Loading(
                    id="loading-historical-top-pairs",
                    type="circle",
                    children=[
                        dcc.Graph(
                            id='historical-top-pairs-chart',
                            figure=go.Figure(
                                layout=go.Layout(
                                    title=dict(text="Color-Coded Cumulative Combined Capture Chart", font=dict(color='#80ff00')),
                                    plot_bgcolor='black',
                                    paper_bgcolor='black',
                                    font=dict(color='#80ff00'),
                                    xaxis=dict(visible=False),
                                    yaxis=dict(visible=False),
                                    template='plotly_dark'
                                )
                            )
                        )
                    ]
                )
            ], width=12)
        ]),
        # Row for toggles
        dbc.Row([
            dbc.Col([
                dbc.Switch(
                    id='show-annotations-toggle',
                    label='Show Signal Annotations',
                    value=False  # Default to hiding annotations
                ),
                dbc.Switch(
                    id='display-top-pairs-toggle',
                    label='Display All Top Pair Traces',
                    value=False  # Default to hiding top pair traces
                )
            ], width=12)
        ]),
        # Row for generic chart
        dbc.Row([
            dbc.Col([
                dcc.Graph(id='chart')
            ], width=12)
        ]),
        # Row for Buy Pair and Short Pair input cards
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.Div([
                                html.I(className="fas fa-arrow-trend-up me-2", style={"color": "#00ff41"}),
                                'Buy Pair'
                            ], style={"fontSize": "1.25rem", "fontWeight": "bold"}),
                            html.Small('Signal when: SMA₁ value > SMA₂ value', 
                                     style={"color": "#aaa", "fontStyle": "italic"})
                        ])
                    ], style={"color": "#80ff00"}),
                    dbc.CardBody([
                        dbc.Alert([
                            html.I(className="fas fa-info-circle me-2"),
                            "Buy signal triggers when the first SMA's value exceeds the second SMA's value"
                        ], color="info", className="py-2 mb-3", style={"fontSize": "0.9rem"}),
                        html.Div([
                            html.Label(id='sma-input-1-label', className='mb-1'),
                            dcc.Input(id='sma-input-1', type='number', min=1, max=MAX_SMA_DAY, step=1, className='form-control'),
                            html.Div(id='sma-input-1-error', className='text-danger')
                        ], className='mb-3'),
                        html.Div([
                            html.Label(id='sma-input-2-label', className='mb-1'),
                            dcc.Input(id='sma-input-2', type='number', min=1, max=MAX_SMA_DAY, step=1, className='form-control'),
                            html.Div(id='sma-input-2-error', className='text-danger')
                        ], className='mb-3')
                    ])
                ], className='mb-3'),
                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.Div([
                                html.I(className="fas fa-arrow-trend-down me-2", style={"color": "#ff0040"}),
                                'Short Pair'
                            ], style={"fontSize": "1.25rem", "fontWeight": "bold"}),
                            html.Small('Signal when: SMA₁ value < SMA₂ value', 
                                     style={"color": "#aaa", "fontStyle": "italic"})
                        ])
                    ], style={"color": "#80ff00"}),
                    dbc.CardBody([
                        dbc.Alert([
                            html.I(className="fas fa-info-circle me-2"),
                            "Short signal triggers when the first SMA's value is less than the second SMA's value"
                        ], color="danger", className="py-2 mb-3", style={"fontSize": "0.9rem"}),
                        html.Div([
                            html.Label(id='sma-input-3-label', className='mb-1'),
                            dcc.Input(id='sma-input-3', type='number', min=1, max=MAX_SMA_DAY, step=1, className='form-control'),
                            html.Div(id='sma-input-3-error', className='text-danger')
                        ], className='mb-3'),
                        html.Div([
                            html.Label(id='sma-input-4-label', className='mb-1'),
                            dcc.Input(id='sma-input-4', type='number', min=1, max=MAX_SMA_DAY, step=1, className='form-control'),
                            html.Div(id='sma-input-4-error', className='text-danger')
                        ], className='mb-3')
                    ])
                ], className='mb-3')
            ], width=6),
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.Div([
                            html.H5('Your Custom Pair Results', className='mb-0'),
                            html.Small('Live analysis of the SMA pairs entered on the left', 
                                     style={"color": "#aaa", "fontStyle": "italic"})
                        ]),
                        html.Button(children='Maximize', id='toggle-calc-button', className='btn btn-sm btn-secondary ml-auto')
                    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center"}),
                    dbc.Collapse(
                        dbc.CardBody([
                            html.Div([
                                html.I(className="fas fa-arrow-trend-up me-2", style={"color": "#00ff41"}),
                                html.Span(id='buy-pair-header', children='Buy Pair', style={"fontWeight": "bold", "fontSize": "1.1rem"})
                            ], className='mt-3 mb-2'),
                            html.Div(id='trigger-days-buy'),
                            html.Div(id='win-ratio-buy'),
                            html.Div(id='avg-daily-capture-buy'),
                            html.Div(id='total-capture-buy'),
                            html.Div([
                                html.I(className="fas fa-arrow-trend-down me-2", style={"color": "#ff0040"}),
                                html.Span(id='short-pair-header', children='Short Pair', style={"fontWeight": "bold", "fontSize": "1.1rem"})
                            ], className='mt-3 mb-2'),
                            html.Div(id='trigger-days-short'),
                            html.Div(id='win-ratio-short'),
                            html.Div(id='avg-daily-capture-short'),
                            html.Div(id='total-capture-short')
                        ]),
                        id='calc-collapse',
                        is_open=True
                    )
                ])
            ], width=6)
        ]),
        # Row for Dynamic Master Trading Strategy card
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.H5('Dynamic Master Trading Strategy', className='mb-0'),
                        html.Button(children='Maximize', id='toggle-strategy-button', className='btn btn-sm btn-secondary ml-auto')
                    ]),
                    dbc.Collapse(
                        dbc.CardBody([
                            html.Div(id='most-productive-buy-pair'),
                            html.Div(id='most-productive-short-pair'),
                            html.Div(id='avg-capture-buy-leader'),
                            html.Div(id='total-capture-buy-leader'),
                            html.Div(id='avg-capture-short-leader'),
                            html.Div(id='total-capture-short-leader'),
                            html.Div(id='trading-direction'),
                            html.Div(id='performance-expectation'),
                            html.Div(id='confidence-percentage'),
                            html.Div(id='trading-recommendations'),
                            html.Div(id='processing-status'),  # For showing processing status
                            dbc.Progress(
                                id="processing-progress-bar",
                                value=0,
                                striped=True,
                                animated=True,
                                className="mt-2",
                                style={"height": "20px", "display": "none"}
                            )
                        ]),
                        id='strategy-collapse',
                        is_open=False
                    )
                ])
            ], width=12)
        ]),
        # Row for Secondary Ticker input and Signal Following Metrics
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.I(className="fas fa-link me-2"),
                        'Select Secondary Ticker Symbol(s)'
                    ], style={"color": "#80ff00"}),
                    dbc.CardBody([
                        dbc.Input(id='secondary-ticker-input', placeholder='Enter comma-separated tickers (e.g., MSFT, AMZN, ^GSPC)', type='text', debounce=True),
                        dbc.FormFeedback(id='secondary-ticker-input-feedback', className='text-danger')
                    ])
                ], className='mb-3'),
                dbc.Switch(
                    id='invert-signals-toggle',
                    label='Invert Signals',
                    value=False,
                    className='mb-2'
                ),
                dbc.Switch(
                    id='show-secondary-annotations-toggle',
                    label='Show Signal Change Annotations',
                    value=False,
                    className='mb-2'
                ),
                dcc.Loading(
                    id="loading-secondary-capture",
                    type="circle",
                    children=[
                        dcc.Graph(
                            id='secondary-capture-chart',
                            figure=go.Figure(
                                layout=go.Layout(
                                    title=dict(text="Signal Following Performance", font=dict(color='#80ff00')),
                                    plot_bgcolor='black',
                                    paper_bgcolor='black',
                                    font=dict(color='#80ff00'),
                                    xaxis=dict(visible=False),
                                    yaxis=dict(visible=False),
                                    template='plotly_dark'
                                )
                            )
                        ),
                        dbc.Card([
                            dbc.CardHeader([
                                html.I(className="fas fa-chart-bar me-2"),
                                'Signal Following Metrics'
                            ], style={"color": "#80ff00"}),
                            dbc.CardBody([
                                dash_table.DataTable(
                                    id='secondary-metrics-table',
                                    columns=[],  # Will be updated in callback
                                    data=[],     # Will be updated in callback
                                    sort_action='native',
                                    style_table={
                                        'overflowX': 'auto',
                                        'backgroundColor': 'black',
                                    },
                                    style_cell={
                                        'backgroundColor': 'black',
                                        'color': '#80ff00',
                                        'textAlign': 'left',
                                        'minWidth': '50px', 
                                        'width': '100px', 
                                        'maxWidth': '180px',
                                        'whiteSpace': 'normal',
                                        'border': '1px solid #80ff00'
                                    },
                                    style_header={
                                        'backgroundColor': 'black',
                                        'color': '#80ff00',
                                        'fontWeight': 'bold',
                                        'border': '2px solid #80ff00'
                                    },
                                    style_data_conditional=[{
                                        'if': {'row_index': 'odd'},
                                        'backgroundColor': 'rgba(0, 255, 0, 0.05)'
                                    }],
                                )
                            ])
                        ], className='mt-3')
                    ]
                )
            ], width=12)
        ]),
        # New Section: Multi-Primary Signal Aggregator
        html.H2('Multi-Primary Signal Aggregator', className='text-center mt-5'),
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.H5('Aggregate Signals from Multiple Primary Tickers', className='mb-0'),
                        html.Button(children='Maximize', id='toggle-multi-primary-button', className='btn btn-sm btn-secondary ml-auto')
                    ]),
                    dbc.Collapse(
                        dbc.CardBody([
                            # Secondary Ticker Input for Multi-Primary Aggregator
                            html.Div([
                                html.Label("Secondary Ticker (Signal Follower):", className='mb-2'),
                                dbc.Input(
                                    id='multi-secondary-ticker-input',
                                    placeholder='Enter a single ticker (e.g., DJT)',
                                    type='text',
                                    debounce=True
                                ),
                                html.Div(id='multi-secondary-feedback', className='text-danger')
                            ], className='mb-3'),
                            # Primary Tickers Input for Multi-Primary Aggregator
                            html.Div([
                                html.Label("Primary Signal Generators:", className='mb-2'),
                                html.Div([
                                    dbc.Row([
                                        dbc.Col(
                                            dbc.Input(
                                                id={'type': 'primary-ticker-input', 'index': 0},
                                                placeholder='Enter ticker (e.g., CENN)',
                                                type='text',
                                                debounce=True
                                            ),
                                            width=4
                                        ),
                                        dbc.Col(
                                            dbc.Switch(
                                                id={'type': 'invert-primary-switch', 'index': 0},
                                                label='Invert Signals',
                                                value=False
                                            ),
                                            width=2
                                        ),
                                        dbc.Col(
                                            dbc.Switch(
                                                id={'type': 'mute-primary-switch', 'index': 0},
                                                label='Mute',
                                                value=False
                                            ),
                                            width=2
                                        ),
                                        dbc.Col(
                                            dbc.Button(
                                                'Delete',
                                                id={'type': 'delete-primary-button', 'index': 0},
                                                color='danger',
                                                size='sm'
                                            ),
                                            width=2
                                        )
                                    ], className='mb-2', id={'type': 'primary-ticker-row', 'index': 0})
                                ], id='primary-tickers-container'),
                                dbc.Button(
                                    [html.I(className="fas fa-plus me-2"), 'Add Primary Ticker'], 
                                    id='add-primary-button', 
                                    color='success', 
                                    size='sm', 
                                    className='mt-2',
                                    style={"boxShadow": "0 0 10px rgba(0, 255, 65, 0.5)"}
                                )
                            ], className='mb-3'),
                            # Results Display for Multi-Primary Aggregator
                            dcc.Loading(
                                id="loading-multi-primary",
                                type="circle",
                                children=[
                                    dcc.Graph(
                                        id='multi-primary-chart',
                                        figure=go.Figure(
                                            layout=go.Layout(
                                                title=dict(text="Combined Signals Capture Chart", font=dict(color='#80ff00')),
                                                plot_bgcolor='black',
                                                paper_bgcolor='black',
                                                font=dict(color='#80ff00'),
                                                xaxis=dict(visible=False),
                                                yaxis=dict(visible=False),
                                                template='plotly_dark'
                                            )
                                        )
                                    ),
                                    dbc.Card([
                                        dbc.CardHeader([
                                            html.I(className="fas fa-chart-pie me-2"),
                                            'Aggregated Signal Performance'
                                        ], style={"color": "#80ff00"}),
                                        dbc.CardBody([
                                            dash_table.DataTable(
                                                id='multi-primary-metrics-table',
                                                columns=[],  # Will be updated in callback
                                                data=[],     # Will be updated in callback
                                                sort_action='native',
                                                style_table={
                                                    'overflowX': 'auto',
                                                    'backgroundColor': 'black',
                                                },
                                                style_cell={
                                                    'backgroundColor': 'black',
                                                    'color': '#80ff00',
                                                    'textAlign': 'left',
                                                    'minWidth': '50px', 
                                                    'width': '100px', 
                                                    'maxWidth': '180px',
                                                    'whiteSpace': 'normal',
                                                    'border': '1px solid #80ff00'
                                                },
                                                style_header={
                                                    'backgroundColor': 'black',
                                                    'color': '#80ff00',
                                                    'fontWeight': 'bold',
                                                    'border': '2px solid #80ff00'
                                                },
                                                style_data_conditional=[{
                                                    'if': {'row_index': 'odd'},
                                                    'backgroundColor': 'rgba(0, 255, 0, 0.05)'
                                                }],
                                            )
                                        ])
                                    ], className='mt-3')
                                ]
                            )
                        ]),
                        id='multi-primary-collapse',
                        is_open=True
                    )
                ], className='mb-3')
            ], width=12)
        ]),
        # Ticker Batch Process Section
        html.H2('Ticker Batch Process', className='text-center mt-5'),
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.I(className="fas fa-tasks me-2"),
                        'Enter Tickers to Batch Process'
                    ], style={"color": "#80ff00"}),
                    dbc.CardBody([
                        dbc.Textarea(
                            id='batch-ticker-input',
                            placeholder='Enter ticker symbols separated by commas (e.g., AAPL, MSFT, GOOG)',
                            style={'width': '100%', 'height': '100px'}
                        ),
                        dbc.Button(
                            [html.I(className="fas fa-play me-2"), 'Process Tickers'], 
                            id='batch-process-button', 
                            color='primary', 
                            className='mt-2',
                            style={"boxShadow": "0 0 15px rgba(128, 255, 0, 0.5)"}
                        ),
                        dbc.FormFeedback(id='batch-ticker-input-feedback', className='text-danger')
                    ])
                ], className='mb-3'),
                dcc.Loading(
                    id="loading-batch-process",
                    type="circle",
                    children=[
                        dash_table.DataTable(
                            id='batch-process-table',
                            columns=[
                                {'name': 'Ticker', 'id': 'Ticker'},
                                {'name': 'Last Date', 'id': 'Last Date'},
                                {'name': 'Last Price', 'id': 'Last Price'},
                                {'name': 'Next Day Active Signal', 'id': 'Next Day Active Signal'},
                                {'name': 'Processing Status', 'id': 'Processing Status'}
                            ],
                            data=[],
                            style_table={
                                'overflowX': 'auto',
                                'backgroundColor': 'black',
                            },
                            style_cell={
                                'backgroundColor': 'black',
                                'color': '#80ff00',
                                'textAlign': 'left',
                                'minWidth': '50px',
                                'width': '100px',
                                'maxWidth': '180px',
                                'whiteSpace': 'normal',
                                'border': '1px solid #80ff00'
                            },
                            style_header={
                                'backgroundColor': 'black',
                                'color': '#80ff00',
                                'fontWeight': 'bold',
                                'border': '2px solid #80ff00'
                            },
                            style_data_conditional=[{
                                'if': {'row_index': 'odd'},
                                'backgroundColor': 'rgba(0, 255, 0, 0.05)'
                            }],
                        )
                    ]
                )
            ], width=12)
        ]),
        # Automated Signal Optimization Section
        html.H2('Automated Signal Optimization', className='text-center mt-5'),
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader([
                        html.I(className="fas fa-magic me-2"),
                        'Optimize Primary Signals for Secondary Ticker'
                    ], style={"color": "#80ff00"}),
                    dbc.CardBody([
                        # Input for secondary ticker (Signal Follower)
                        html.Div([
                            dbc.Label('Enter Secondary Ticker (Signal Follower):'),
                            dbc.Input(
                                id='optimization-secondary-ticker',
                                placeholder='e.g., SPY',
                                type='text',
                                debounce=True
                            ),
                        ]),
                        # Input for primary tickers (Signal Generators)
                        html.Div([
                            dbc.Label('Enter Primary Tickers (Signal Generators, comma-separated):'),
                            dbc.Input(
                                id='optimization-primary-tickers',
                                placeholder='e.g., AAPL, MSFT, GOOG',
                                type='text',
                                debounce=True
                            ),
                        ]),
                        # Button to start optimization
                        dbc.Button(
                            [html.I(className="fas fa-magic me-2"), 'Optimize Signals'], 
                            id='optimize-signals-button', 
                            color='primary', 
                            className='mt-2',
                            style={"boxShadow": "0 0 15px rgba(128, 255, 0, 0.5)"}
                        ),
                        # Feedback message
                        html.Div(id='optimization-feedback', className='text-danger mt-2'),
                    ])
                ], className='mb-3'),
                # Loading spinner and results table for optimization
                dcc.Loading(
                    id="loading-optimization",
                    type="circle",
                    children=[
                        # Table to display results
                        dash_table.DataTable(
                            id='optimization-results-table',
                            columns=[
                                {'name': 'Combination', 'id': 'Combination', 'presentation': 'markdown'},
                                {'name': 'Trigger Days', 'id': 'Trigger Days', 'type': 'numeric'},
                                {'name': 'Wins', 'id': 'Wins', 'type': 'numeric'},
                                {'name': 'Losses', 'id': 'Losses', 'type': 'numeric'},
                                {'name': 'Win Ratio (%)', 'id': 'Win Ratio (%)', 'type': 'numeric'},
                                {'name': 'Std Dev (%)', 'id': 'Std Dev (%)', 'type': 'numeric'},
                                {'name': 'Sharpe Ratio', 'id': 'Sharpe Ratio', 'type': 'numeric'},
                                {'name': 't-Statistic', 'id': 't-Statistic'},
                                {'name': 'p-Value', 'id': 'p-Value'},
                                {'name': 'Significant 90%', 'id': 'Significant 90%'},
                                {'name': 'Significant 95%', 'id': 'Significant 95%'},
                                {'name': 'Significant 99%', 'id': 'Significant 99%'},
                                {'name': 'Avg Daily Capture (%)', 'id': 'Avg Daily Capture (%)', 'type': 'numeric'},
                                {'name': 'Total Capture (%)', 'id': 'Total Capture (%)', 'type': 'numeric'}
                            ],
                            data=[],
                            sort_action='custom',
                            sort_mode='multi',
                            sort_by=[],
                            persistence=True,
                            persistence_type='session',
                            markdown_options={'html': True},  # Enable HTML rendering in markdown cells
                            style_data={'whiteSpace': 'normal', 'height': 'auto'},
                            cell_selectable=True,
                            selected_cells=[],
                            style_table={
                                'overflowX': 'auto',
                                'backgroundColor': 'black',
                            },
                            style_cell={
                                'backgroundColor': 'black',
                                'color': '#80ff00',
                                'textAlign': 'left',
                                'minWidth': '50px', 
                                'width': '100px', 
                                'maxWidth': '180px',
                                'whiteSpace': 'normal',
                                'border': '1px solid #80ff00'
                            },
                            style_header={
                                'backgroundColor': 'black',
                                'color': '#80ff00',
                                'fontWeight': 'bold',
                                'border': '2px solid #80ff00'
                            },
                            style_data_conditional=[
                                {
                                    'if': {'row_index': 'odd'},
                                    'backgroundColor': 'rgba(0, 255, 0, 0.05)'
                                },
                                {
                                    'if': {'state': 'selected'},
                                    'backgroundColor': 'rgba(0, 255, 0, 0.2)',
                                    'border': '2px solid #80ff00'
                                },
                                {
                                    'if': {'filter_query': '{Combination} = "AVERAGES"'},
                                    'backgroundColor': 'rgba(0, 255, 0, 0.15)',
                                    'fontWeight': 'bold',
                                    'border-bottom': '2px solid #80ff00'
                                }
                            ],
                        )
                    ]
                )
            ], width=12)
        ]),
        # Interval components for periodic updates
        dcc.Interval(id='batch-update-interval', interval=5000, n_intervals=0),
        dcc.Interval(id='update-interval', interval=5000, n_intervals=0, disabled=False),  # Decreased to 5 seconds from 30 seconds
        dcc.Interval(id='loading-interval', interval=5000, n_intervals=0),  # Update every 5 seconds
        dcc.Interval(id='optimization-update-interval', interval=5000, n_intervals=0, disabled=True),
        # Loading spinner output (if needed)
        dcc.Loading(
            id="loading-spinner",
            type="circle",
            color="#80ff00",
            children=[html.Div(id="loading-spinner-output")]
        ),
        # Notification container
        html.Div(id="notification-container", style={
            "position": "fixed",
            "top": "80px",
            "right": "20px",
            "zIndex": "1001",
            "maxWidth": "400px"
        }),
        # Enhanced Footer
        html.Hr(style={"borderColor": "#80ff00", "borderWidth": "2px", "opacity": "0.5", "marginTop": "50px"}),
        html.Div([
            html.P([
                html.I(className="fas fa-atom me-2", style={"animation": "spin 6s linear infinite"}),
                "PRJCT9 | Advanced Trading Analysis Platform",
                html.Span(" | ", style={"color": "#666"}),
                "Built by ", html.Strong("Rebel Atom LLC", style={"color": "#80ff00"})
            ], className="text-center", style={"color": "#80ff00", "fontSize": "14px"}),
            html.P([
                "© 2025 Rebel Atom LLC. All rights reserved. ",
                html.Span("Version 2.0", className="badge bg-success ms-2")
            ], className="text-center text-muted", style={"fontSize": "12px", "marginTop": "10px"})
        ], style={"marginBottom": "30px"})
    ]
)

# ============================================================================
# CALLBACKS - UI INTERACTION HANDLERS
# ============================================================================

# -----------------------------------------------------------------------------
# Ticker and SMA Display Callbacks
# -----------------------------------------------------------------------------
@app.callback(
    Output('max-sma-day-display', 'children'),
    [Input('ticker-input', 'value')]
)
def update_max_sma_day_display(ticker):
    if not ticker:
        return 'Please enter a ticker symbol to get started.'

    results = load_precomputed_results(ticker)
    if results is None:
        return 'Loading data...'

    MAX_SMA_DAY = results.get('existing_max_sma_day', 'N/A')
    return f"Current MAX_SMA_DAY for {ticker.upper()}: {MAX_SMA_DAY}"

@app.callback(
    [Output('sma-input-1', 'max'),
     Output('sma-input-2', 'max'),
     Output('sma-input-3', 'max'),
     Output('sma-input-4', 'max'),
     Output('sma-input-1-label', 'children'),
     Output('sma-input-2-label', 'children'),
     Output('sma-input-3-label', 'children'),
     Output('sma-input-4-label', 'children')],
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')]
)
def update_sma_labels(ticker, n_intervals):
    if not ticker:
        trading_days = 1
    else:
        df = fetch_data(ticker)
        if df is None or df.empty:
            trading_days = 1
        else:
            trading_days = len(df)
        
        results = load_precomputed_results(ticker)
        if results is not None:
            preprocessed_df = results.get('preprocessed_data')
            if preprocessed_df is not None and not preprocessed_df.empty:
                trading_days = max(trading_days, len(preprocessed_df))

    max_values = [trading_days] * 4
    labels = [
        f"Enter 1st SMA Day (1-{trading_days}) for Buy Pair:",
        f"Enter 2nd SMA Day (1-{trading_days}) for Buy Pair:",
        f"Enter 1st SMA Day (1-{trading_days}) for Short Pair:",
        f"Enter 2nd SMA Day (1-{trading_days}) for Short Pair:"
    ]

    return max_values + labels

@app.callback(
    Output('processing-status', 'children'),
    [Input('update-interval', 'n_intervals')],
    [State('ticker-input', 'value')]
)
def update_processing_status(n_intervals, ticker):
    if not ticker:
        return "Enter a ticker to start."
    
    status = read_status(ticker)
    if status['status'] == 'processing':
        return f"Processing data for {ticker}... Progress: {status['progress']:.2f}%"
    elif status['status'] == 'complete':
        return f"Data processing complete for {ticker}."
    elif status['status'] == 'failed':
        return f"Data processing failed for {ticker}. Please try again."
    else:
        results = load_precomputed_results(ticker)
        if results is None:
            return f"Loading data for {ticker}..."
        else:
            return f"Data loaded for {ticker}."

@app.callback(
    [Output('calc-collapse', 'is_open'),
     Output('toggle-calc-button', 'children')],
    [Input('toggle-calc-button', 'n_clicks')],
    [State('calc-collapse', 'is_open')],
)
def toggle_calc_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open, 'Minimize' if not is_open else 'Maximize'
    return is_open, 'Maximize' if not is_open else 'Minimize'

# Callback to toggle the visibility of the Dynamic Master Trading Strategy section
@app.callback(
    [Output('strategy-collapse', 'is_open'),
     Output('toggle-strategy-button', 'children')],
    [Input('toggle-strategy-button', 'n_clicks')],
    [State('strategy-collapse', 'is_open')],
)
def toggle_strategy_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open, 'Minimize' if not is_open else 'Maximize'
    return is_open, 'Maximize' if not is_open else 'Minimize'

@app.callback(
    [Output('sma-input-1', 'className'),
     Output('sma-input-2', 'className'),
     Output('sma-input-3', 'className'),
     Output('sma-input-4', 'className'),
     Output('sma-input-1-error', 'children'),
     Output('sma-input-2-error', 'children'),
     Output('sma-input-3-error', 'children'),
     Output('sma-input-4-error', 'children')],
    [Input('sma-input-1', 'value'),
     Input('sma-input-2', 'value'),
     Input('sma-input-3', 'value'),
     Input('sma-input-4', 'value'),
     Input('ticker-input', 'value')]
)
def validate_sma_inputs(sma_input_1, sma_input_2, sma_input_3, sma_input_4, ticker):
    sma_inputs = [sma_input_1, sma_input_2, sma_input_3, sma_input_4]
    input_classes = []
    error_messages = []

    if ticker:
        df = fetch_data(ticker)
        trading_days = len(df) if df is not None and not df.empty else 1
    else:
        trading_days = 1

    for sma_input in sma_inputs:
        if sma_input is None or sma_input < 1 or sma_input > trading_days:
            input_classes.append('form-control is-invalid')
            error_messages.append(f'Please enter a valid SMA day (1-{trading_days}).')
        else:
            input_classes.append('form-control')
            error_messages.append('')

    return input_classes + error_messages

def get_existing_max_sma_day(df):
    sma_columns = [col for col in df.columns if 'SMA_' in col]
    
    if not sma_columns:
        return 0
    
    # Extract the SMA day from each column name and convert to int
    sma_days = [int(col.split('_')[1]) for col in sma_columns]
    
    # Return the maximum SMA day
    return max(sma_days)

@app.callback(
    [Output('ticker-input-feedback', 'children'),
     Output('ticker-input', 'valid'),
     Output('ticker-input', 'invalid')],
    [Input('ticker-input', 'value')]
)
def validate_ticker_input(ticker):
    if not ticker:
        return '', False, False

    if not ticker.strip():  # Check for whitespace-only input
        return 'Please enter a ticker symbol.', False, True

    ticker = normalize_ticker(ticker)
    
    # Ticker input will be logged when processing starts
    
    df = fetch_data(ticker)
    
    if df is None or df.empty:
        logger.error(f"Invalid ticker '{ticker}' - no data available from yfinance")
        return f"Invalid ticker '{ticker}' entered. Please enter a valid yfinance ticker.", False, True

    results = get_data(ticker, MAX_SMA_DAY)
    if results is None:
        # Data loading message already shown in precompute_results
        return 'Loading data...', False, False
    else:
        logger.info(f"{Colors.OKGREEN}[✅] Data ready for {ticker.upper()}{Colors.ENDC}")

    return '', True, False

def calculate_cumulative_combined_capture(df, daily_top_buy_pairs, daily_top_short_pairs):
    logger.info("Calculating cumulative combined capture")

    if not daily_top_buy_pairs or not daily_top_short_pairs:
        logger.warning("No daily top pairs available for processing cumulative combined captures.")
        return pd.Series([0], index=[df.index[0]]), ['None']

    # Ensure daily_top_pairs have matching lengths
    dates = sorted(set(daily_top_buy_pairs.keys()) & set(daily_top_short_pairs.keys()))
    if not dates:
        logger.warning("No overlapping dates between buy and short pairs")
        return pd.Series([0], index=[df.index[0]]), ['None']
    
    # Verify data integrity
    for date in dates:
        if not isinstance(daily_top_buy_pairs[date][0], tuple) or not isinstance(daily_top_short_pairs[date][0], tuple):
            logger.warning(f"Invalid pair format found for date {date}")
            return pd.Series([0], index=[df.index[0]]), ['None']

    cumulative_combined_captures = []
    active_pairs = []
    cumulative_capture = 0

    logger.info("Calculating cumulative combined capture...")
    with logging_redirect_tqdm():
        with tqdm(total=len(dates), desc="Calculating cumulative combined captures", unit="day", dynamic_ncols=True, mininterval=0.1, leave=True, position=0) as pbar:
            for i in range(len(dates)):
                current_date = dates[i]

                if i == 0:
                    previous_date = current_date
                    current_position = 'None'
                    daily_capture = 0
                else:
                    previous_date = dates[i - 1]

                    prev_buy_pair, prev_buy_capture = daily_top_buy_pairs[previous_date]
                    prev_short_pair, prev_short_capture = daily_top_short_pairs[previous_date]

                    if prev_buy_pair == (0, 0) or prev_short_pair == (0, 0):
                        current_position = 'None'
                    else:
                        buy_signal = df[f'SMA_{prev_buy_pair[0]}'].loc[previous_date] > df[f'SMA_{prev_buy_pair[1]}'].loc[previous_date]
                        short_signal = df[f'SMA_{prev_short_pair[0]}'].loc[previous_date] < df[f'SMA_{prev_short_pair[1]}'].loc[previous_date]

                        if buy_signal and short_signal:
                            if prev_buy_capture > prev_short_capture:
                                current_position = f"Buy {prev_buy_pair[0]},{prev_buy_pair[1]}"
                            else:
                                current_position = f"Short {prev_short_pair[0]},{prev_short_pair[1]}"
                        elif buy_signal:
                            current_position = f"Buy {prev_buy_pair[0]},{prev_buy_pair[1]}"
                        elif short_signal:
                            current_position = f"Short {prev_short_pair[0]},{prev_short_pair[1]}"
                        else:
                            current_position = "None"

                    daily_return = df['Close'].loc[current_date] / df['Close'].loc[previous_date] - 1

                    if current_position.startswith('Buy'):
                        daily_capture = daily_return * 100
                    elif current_position.startswith('Short'):
                        daily_capture = -daily_return * 100
                    else:
                        daily_capture = 0

                cumulative_capture += daily_capture
                cumulative_combined_captures.append(cumulative_capture)
                active_pairs.append(current_position)

                # Log current top pairs and results every 1000 days
                if (i + 1) % 1000 == 0 or i == len(dates) - 1:
                    current_buy_pair = daily_top_buy_pairs[dates[i]][0]
                    current_short_pair = daily_top_short_pairs[dates[i]][0]
                    current_capture = cumulative_combined_captures[-1]
                    tqdm.write(f"Day {i+1}: Top Buy Pair: {current_buy_pair}, Top Short Pair: {current_short_pair}, Cumulative Capture: {current_capture:.2f}%")

                pbar.update(1)
    
    # Add line break after progress bar
    logger.info("")
    
    # After the loop, print a summary
    logger.info("Cumulative Capture Summary:")
    logger.info(f"Date range: {dates[0]} to {dates[-1]}")
    logger.info(f"Total Trading Days: {len(dates)}")
    log_separator()
    logger.info(f"Final Cumulative Capture: {cumulative_capture:.2f}%")
    log_separator()

    return pd.Series(cumulative_combined_captures, index=dates), active_pairs

def get_or_calculate_combined_captures(results, df, daily_top_buy_pairs, daily_top_short_pairs, ticker):
    if 'cumulative_combined_captures' in results and 'active_pairs' in results:
        cumulative_combined_captures = results['cumulative_combined_captures']
        active_pairs = results['active_pairs']
        logger.info("Using stored cumulative_combined_captures and active_pairs")
    else:
        # Ensure daily_top_buy_pairs and daily_top_short_pairs are in the correct format
        formatted_daily_top_buy_pairs = {}
        formatted_daily_top_short_pairs = {}

        for date, (pair, capture) in daily_top_buy_pairs.items():
            if isinstance(pair, tuple) and len(pair) == 2:
                formatted_daily_top_buy_pairs[date] = (pair, capture)
            elif isinstance(pair, int):
                formatted_daily_top_buy_pairs[date] = ((pair, capture), 0)
            else:
                print(f"Unexpected buy pair format for date {date}: {pair}")

        for date, (pair, capture) in daily_top_short_pairs.items():
            if isinstance(pair, tuple) and len(pair) == 2:
                formatted_daily_top_short_pairs[date] = (pair, capture)
            elif isinstance(pair, int):
                formatted_daily_top_short_pairs[date] = ((pair, capture), 0)
            else:
                print(f"Unexpected short pair format for date {date}: {pair}")

        cumulative_combined_captures, active_pairs = calculate_cumulative_combined_capture(
            df, formatted_daily_top_buy_pairs, formatted_daily_top_short_pairs
        )
        logger.info("Calculated new cumulative_combined_captures and active_pairs")

        # Update the results dictionary with the new data
        results['cumulative_combined_captures'] = cumulative_combined_captures
        results['active_pairs'] = active_pairs
        save_precomputed_results(ticker, results)

    logger.info(f"Number of cumulative combined captures: {len(cumulative_combined_captures)}")
    logger.info(f"Number of active pairs: {len(active_pairs)}")

    return cumulative_combined_captures, active_pairs

def prepare_historical_top_pairs_data(df, daily_top_buy_pairs, daily_top_short_pairs, buy_results, short_results, cumulative_combined_captures):
    dates = sorted(daily_top_buy_pairs.keys())
    
    top_pairs = set()
    top_pairs_performance = {}

    for date in dates:
        buy_pair, _ = daily_top_buy_pairs[date]
        short_pair, _ = daily_top_short_pairs[date]

        top_pairs.add(('Buy', buy_pair))
        top_pairs.add(('Short', short_pair))

    # Initialize performance series for all top pairs
    for pair_type, pair in top_pairs:
        if pair_type == 'Buy':
            if pair in buy_results:
                top_pairs_performance[f'Buy {pair}'] = buy_results[pair]
            elif (pair[1], pair[0]) in short_results:  # Check for inverse pair
                top_pairs_performance[f'Buy {pair}'] = -short_results[(pair[1], pair[0])]
        else:  # Short pair
            if pair in short_results:
                top_pairs_performance[f'Short {pair}'] = short_results[pair]
            elif (pair[1], pair[0]) in buy_results:  # Check for inverse pair
                top_pairs_performance[f'Short {pair}'] = -buy_results[(pair[1], pair[0])]

    return cumulative_combined_captures, top_pairs_performance

def load_and_prepare_data(ticker):
    results = load_precomputed_results(ticker)
    if results is None:
        logger.debug(f"Data for ticker {ticker} is still loading.")
        return None, None, None, None, None, None
    
    # Enhanced validation of required data
    required_keys = ['preprocessed_data', 'daily_top_buy_pairs', 'daily_top_short_pairs', 
                    'top_buy_pair', 'top_short_pair']
    missing_keys = [key for key in required_keys if key not in results]
    
    if missing_keys:
        logger.error(f"Missing required keys in results for {ticker}: {missing_keys}")
        return None, None, None, None, None, None
    
    # Validate top pairs format
    if not isinstance(results['top_buy_pair'], tuple) or not isinstance(results['top_short_pair'], tuple):
        logger.error(f"Invalid top pairs format for {ticker}")
        return None, None, None, None, None, None
        
    # Validate data structure
    df = results['preprocessed_data']
    daily_top_buy_pairs = results['daily_top_buy_pairs']
    daily_top_short_pairs = results['daily_top_short_pairs']
    
    # Ensure length matches
    if len(df) != len(daily_top_buy_pairs) or len(df) != len(daily_top_short_pairs):
        logger.error(f"Length mismatch in data for {ticker}")
        logger.error(f"DataFrame length: {len(df)}")
        logger.error(f"Buy pairs length: {len(daily_top_buy_pairs)}")
        logger.error(f"Short pairs length: {len(daily_top_short_pairs)}")
        return None, None, None, None, None, None
    
    df = results['preprocessed_data']
    daily_top_buy_pairs = results.get('daily_top_buy_pairs', {})
    daily_top_short_pairs = results.get('daily_top_short_pairs', {})
    cumulative_combined_captures = results.get('cumulative_combined_captures', pd.Series())
    active_pairs = results.get('active_pairs', [])
    
    # Silent load - no logging in callback
    
    # Only calculate if not already present in results
    if 'cumulative_combined_captures' not in results or 'active_pairs' not in results:
        cumulative_combined_captures, active_pairs = get_or_calculate_combined_captures(
            results=results,
            df=df,
            daily_top_buy_pairs=daily_top_buy_pairs,
            daily_top_short_pairs=daily_top_short_pairs,
            ticker=ticker
        )
    
    return results, df, daily_top_buy_pairs, daily_top_short_pairs, cumulative_combined_captures, active_pairs

# -----------------------------------------------------------------------------
# Chart Update Callbacks
# -----------------------------------------------------------------------------
@app.callback(
    Output('combined-capture-chart', 'figure'),
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')]
)
def update_combined_capture_chart(ticker, n_intervals):
    if not ticker:
        return no_update  # Do not update the chart

    status = read_status(ticker)
    if status['status'] != 'complete':
        # Data is not ready yet
        return no_update  # Do not update the chart

    results = load_precomputed_results(ticker)
    if results is None:
        return no_update  # Do not update the chart

    results, df, daily_top_buy_pairs, daily_top_short_pairs, cumulative_combined_captures, active_pairs = load_and_prepare_data(ticker)
    if results is None or df is None or daily_top_buy_pairs is None or daily_top_short_pairs is None or cumulative_combined_captures is None or active_pairs is None:
        return no_update  # Do not update the chart

    if len(cumulative_combined_captures) == 1 and active_pairs == ['None']:
        return no_update  # Do not update the chart

    data = pd.DataFrame({
        'date': cumulative_combined_captures.index,
        'capture': cumulative_combined_captures,
        'top_buy_pair': [
            f"SMA {daily_top_buy_pairs[date][0][0]} / SMA {daily_top_buy_pairs[date][0][1]} ({daily_top_buy_pairs[date][1]:.2f}%)"
            if date in daily_top_buy_pairs and isinstance(daily_top_buy_pairs[date][0], tuple)
            else f"SMA {daily_top_buy_pairs[date][0]} / SMA {daily_top_buy_pairs[date][1]} ({daily_top_buy_pairs[date][1]:.2f}%)"
            if date in daily_top_buy_pairs
            else "No Data"
            for date in cumulative_combined_captures.index
        ],
        'top_short_pair': [
            f"SMA {daily_top_short_pairs[date][0][0]} / SMA {daily_top_short_pairs[date][0][1]} ({daily_top_short_pairs[date][1]:.2f}%)"
            if date in daily_top_short_pairs and isinstance(daily_top_short_pairs[date][0], tuple)
            else f"SMA {daily_top_short_pairs[date][0]} / SMA {daily_top_short_pairs[date][1]} ({daily_top_short_pairs[date][1]:.2f}%)"
            if date in daily_top_short_pairs
            else "No Data"
            for date in cumulative_combined_captures.index
        ],
        'active_pair_current': active_pairs,
        'active_pair_next': active_pairs[1:] + ['']  # Placeholder for the last day
    })

    # Calculate the next day's active pair for the last day with enhanced validation
    last_date = data['date'].iloc[-1]
    buy_pair_data = daily_top_buy_pairs.get(last_date)
    short_pair_data = daily_top_short_pairs.get(last_date)
    
    if buy_pair_data is None or short_pair_data is None:
        logger.error(f"Missing pair data for last date {last_date}")
        return no_update
        
    top_buy_pair = buy_pair_data[0] if isinstance(buy_pair_data, tuple) else (0, 0)
    top_short_pair = short_pair_data[0] if isinstance(short_pair_data, tuple) else (0, 0)
    
    if not isinstance(top_buy_pair, tuple) or not isinstance(top_short_pair, tuple):
        logger.error(f"Invalid pair format for {last_date}")
        return no_update

    if top_buy_pair and top_buy_pair[0] != 0 and top_buy_pair[1] != 0 and top_short_pair and top_short_pair[0] != 0 and top_short_pair[1] != 0:
        if last_date in df.index:
            # Use data corresponding to last_date
            buy_signal = df[f'SMA_{top_buy_pair[0]}'].loc[last_date] > df[f'SMA_{top_buy_pair[1]}'].loc[last_date]
            short_signal = df[f'SMA_{top_short_pair[0]}'].loc[last_date] < df[f'SMA_{top_short_pair[1]}'].loc[last_date]
        else:
            # Handle case where last_date is not in df.index
            buy_signal = False
            short_signal = False
        
        if buy_signal and short_signal:
            buy_capture = daily_top_buy_pairs.get(last_date, (None, 0))[1]
            short_capture = daily_top_short_pairs.get(last_date, (None, 0))[1]
            if buy_capture > short_capture:
                data.loc[data.index[-1], 'active_pair_next'] = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
            else:
                data.loc[data.index[-1], 'active_pair_next'] = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
        elif buy_signal:
            data.loc[data.index[-1], 'active_pair_next'] = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
        elif short_signal:
            data.loc[data.index[-1], 'active_pair_next'] = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
        else:
            data.loc[data.index[-1], 'active_pair_next'] = "None"
    else:
        data.loc[data.index[-1], 'active_pair_next'] = "None"

    # Commented out sample data display to reduce log clutter
    # logger.debug(f"Sample data rows:\n{data.head(10)}\n{data.tail(10)}")

    # Calculate the active pair for the upcoming trading session
    last_date = df.index[-1]
    if last_date in daily_top_buy_pairs and last_date in daily_top_short_pairs:
        top_buy_pair = daily_top_buy_pairs[last_date][0]
        top_short_pair = daily_top_short_pairs[last_date][0]

        if top_buy_pair and top_buy_pair[0] != 0 and top_buy_pair[1] != 0 and top_short_pair and top_short_pair[0] != 0 and top_short_pair[1] != 0:
            buy_signal = df[f'SMA_{top_buy_pair[0]}'].iloc[-1] > df[f'SMA_{top_buy_pair[1]}'].iloc[-1]
            short_signal = df[f'SMA_{top_short_pair[0]}'].iloc[-1] < df[f'SMA_{top_short_pair[1]}'].iloc[-1]

            if buy_signal and short_signal:
                if daily_top_buy_pairs[last_date][1] > daily_top_short_pairs[last_date][1]:
                    next_active_pair = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
                else:
                    next_active_pair = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
            elif buy_signal:
                next_active_pair = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
            elif short_signal:
                next_active_pair = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
            else:
                next_active_pair = "None"
        else:
            next_active_pair = "None"
    else:
        next_active_pair = "None"
    
    # Active pair info will be shown in statistical analysis section

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=data['date'],
        y=data['capture'],
        mode='lines',
        name='Combined Capture',
        hovertemplate=(
            'Date: %{x}<br>'
            'Cumulative Combined Capture: %{y:.2f}%<br>'
            'Top Buy Pair: %{customdata[0]}<br>'
            'Top Short Pair: %{customdata[1]}<br>'
            'Active Pair for Current Day: %{customdata[2]}<br>'
            'Active Pair for Next Day: %{customdata[3]}'
            '<extra></extra>'
        ),
        customdata=data[['top_buy_pair', 'top_short_pair', 'active_pair_current', 'active_pair_next']],
        line=dict(color='#00eaff'),
    ))

    fig.update_layout(
        title=dict(
            text=f'{ticker.upper()} Cumulative Combined Capture Chart',
            font=dict(color='#80ff00')
        ),
        xaxis_title='Trading Day',
        yaxis_title='Cumulative Combined Capture (%)',
        hovermode='x',
        template='plotly_dark',
        font=dict(color='#80ff00'),
        plot_bgcolor='black',
        paper_bgcolor='black',
        xaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        ),
        yaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        )
    )

    return fig

@app.callback(
    Output('historical-top-pairs-chart', 'figure'),
    [Input('ticker-input', 'value'),
     Input('show-annotations-toggle', 'value'),
     Input('display-top-pairs-toggle', 'value'),
     Input('update-interval', 'n_intervals')]
)
def update_historical_top_pairs_chart(ticker, show_annotations, display_top_pairs, n_intervals):
    if not ticker:
        return no_update  # Do not update the chart

    # Check if data processing is complete
    status = read_status(ticker)
    if status['status'] != 'complete':
        return no_update  # Do not update the chart

    # Proceed only if data is ready
    try:
        results = load_precomputed_results(ticker)
        if results is None:
            return no_update  # Do not update the chart

        # Ensure required keys exist before accessing
        required_keys = [
            'preprocessed_data',
            'daily_top_buy_pairs',
            'daily_top_short_pairs',
            'cumulative_combined_captures',
            'active_pairs'
        ]
        missing_keys = [k for k in required_keys if k not in results]
        if missing_keys:
            logger.error(f"Missing required keys in results for {ticker}: {missing_keys}")
            # Return no_update since we cannot proceed without these keys
            return no_update

        # Extract required data from results
        df = results['preprocessed_data']
        daily_top_buy_pairs = results['daily_top_buy_pairs']
        daily_top_short_pairs = results['daily_top_short_pairs']
        cumulative_combined_captures = results['cumulative_combined_captures']
        active_pairs = results['active_pairs']

        # Data already loaded - no logging needed in callback

        fig = go.Figure()

        if display_top_pairs:
            # Collect all unique buy and short pairs
            top_buy_pairs_set = set([daily_top_buy_pairs[date][0] for date in daily_top_buy_pairs])
            top_short_pairs_set = set([daily_top_short_pairs[date][0] for date in daily_top_short_pairs])

            # Compute total capture for each buy pair
            buy_pair_performance = {}
            for pair in top_buy_pairs_set:
                try:
                    sma1 = df[f'SMA_{pair[0]}']
                    sma2 = df[f'SMA_{pair[1]}']
                    signals = sma1 > sma2
                    signals_shifted = signals.shift(1, fill_value=False)
                    returns = df['Close'].pct_change()
                    pair_returns = returns.where(signals_shifted, 0)
                    cumulative_capture = pair_returns.cumsum() * 100
                    total_capture = cumulative_capture.iloc[-1]
                    buy_pair_performance[pair] = total_capture
                except Exception as e:
                    logger.error(f"Error processing Buy pair {pair}: {str(e)}")

            # Compute total capture for each short pair
            short_pair_performance = {}
            for pair in top_short_pairs_set:
                try:
                    sma1 = df[f'SMA_{pair[0]}']
                    sma2 = df[f'SMA_{pair[1]}']
                    signals = sma1 < sma2
                    signals_shifted = signals.shift(1, fill_value=False)
                    returns = -df['Close'].pct_change()
                    pair_returns = returns.where(signals_shifted, 0)
                    cumulative_capture = pair_returns.cumsum() * 100
                    total_capture = cumulative_capture.iloc[-1]
                    short_pair_performance[pair] = total_capture
                except Exception as e:
                    logger.error(f"Error processing Short pair {pair}: {str(e)}")

            # For buy pairs, calculate median performance
            buy_performances = list(buy_pair_performance.values())
            if buy_performances:
                median_buy_performance = np.median(buy_performances)
                max_buy_deviation = max(abs(perf - median_buy_performance) for perf in buy_performances)
            else:
                median_buy_performance = 0
                max_buy_deviation = 1  # Avoid division by zero

            # For short pairs, calculate median performance
            short_performances = list(short_pair_performance.values())
            if short_performances:
                median_short_performance = np.median(short_performances)
                max_short_deviation = max(abs(perf - median_short_performance) for perf in short_performances)
            else:
                median_short_performance = 0
                max_short_deviation = 1  # Avoid division by zero

            # For each buy pair, add trace with color intensity based on deviation from median
            for pair, total_capture in buy_pair_performance.items():
                try:
                    # Calculate deviation from median
                    deviation = abs(total_capture - median_buy_performance)
                    # Normalize deviation to get intensity
                    intensity = deviation / max_buy_deviation if max_buy_deviation != 0 else 1
                    # Map intensity to color (dimmer for middle performers)
                    green_value = int(50 + intensity * 205)  # From 50 to 255
                    color = f'rgb(0,{green_value},0)'

                    sma1 = df[f'SMA_{pair[0]}']
                    sma2 = df[f'SMA_{pair[1]}']
                    signals = sma1 > sma2
                    signals_shifted = signals.shift(1, fill_value=False)
                    returns = df['Close'].pct_change()
                    pair_returns = returns.where(signals_shifted, 0)
                    cumulative_capture = pair_returns.cumsum() * 100

                    fig.add_trace(go.Scatter(
                        x=df.index,
                        y=cumulative_capture,
                        mode='lines',
                        name=f'Buy {pair}',
                        line=dict(width=1.5, color=color),
                        opacity=0.8,
                        hoverinfo='skip'
                    ))
                except Exception as e:
                    logger.error(f"Error processing Buy pair {pair}: {str(e)}")

            # For each short pair, add trace with color intensity based on deviation from median
            for pair, total_capture in short_pair_performance.items():
                try:
                    # Calculate deviation from median
                    deviation = abs(total_capture - median_short_performance)
                    # Normalize deviation to get intensity
                    intensity = deviation / max_short_deviation if max_short_deviation != 0 else 1
                    # Map intensity to color (dimmer for middle performers)
                    red_value = int(50 + intensity * 205)  # From 50 to 255
                    color = f'rgb({red_value},0,0)'

                    sma1 = df[f'SMA_{pair[0]}']
                    sma2 = df[f'SMA_{pair[1]}']
                    signals = sma1 < sma2
                    signals_shifted = signals.shift(1, fill_value=False)
                    returns = -df['Close'].pct_change()
                    pair_returns = returns.where(signals_shifted, 0)
                    cumulative_capture = pair_returns.cumsum() * 100

                    fig.add_trace(go.Scatter(
                        x=df.index,
                        y=cumulative_capture,
                        mode='lines',
                        name=f'Short {pair}',
                        line=dict(width=1.5, color=color),
                        opacity=0.8,
                        hoverinfo='skip'
                    ))
                except Exception as e:
                    logger.error(f"Error processing Short pair {pair}: {str(e)}")

        colors = []
        for i in range(len(active_pairs)):
            if i == len(active_pairs) - 1:
                # For the last day, use the current signal
                next_pair = active_pairs[i]
            else:
                # For all other days, use the next day's signal
                next_pair = active_pairs[i + 1]

            if next_pair == 'None':
                colors.append('blue')
            elif next_pair.startswith('Buy'):
                colors.append('green')
            elif next_pair.startswith('Short'):
                colors.append('red')
            else:
                colors.append('gray')  # For any unexpected cases

        # Ensure colors and cumulative_combined_captures have the same length
        if len(colors) < len(cumulative_combined_captures):
            colors.extend([colors[-1]] * (len(cumulative_combined_captures) - len(colors)))
        colors = colors[:len(cumulative_combined_captures)]

        def create_color_segments(colors, cumulative_captures):
            segments = []
            current_color = colors[0]
            start_index = 0

            for i in range(1, len(colors)):
                if colors[i] != current_color:
                    # Include the point at position i-1 to connect segments
                    segments.append({
                        'color': current_color,
                        'x': cumulative_captures.index[start_index:i+1],
                        'y': cumulative_captures.iloc[start_index:i+1]
                    })
                    current_color = colors[i]
                    start_index = i

            # Add the last segment
            segments.append({
                'color': current_color,
                'x': cumulative_captures.index[start_index:],
                'y': cumulative_captures.iloc[start_index:]
            })

            return segments

        color_segments = create_color_segments(colors, cumulative_combined_captures)

        # Add traces for each color segment
        for segment in color_segments:
            fig.add_trace(go.Scatter(
                x=segment['x'],
                y=segment['y'],
                mode='lines',
                line=dict(color=segment['color'], width=2),
                showlegend=False,
                hoverinfo='skip'
            ))

        # Prepare hover information
        next_day_pairs = active_pairs[1:] + ['']  # Shift pairs by one day

        # Calculate the next day's active pair for the last day with enhanced validation
        last_date = cumulative_combined_captures.index[-1]
        buy_pair_data = daily_top_buy_pairs.get(last_date)
        short_pair_data = daily_top_short_pairs.get(last_date)
        
        if not buy_pair_data or not short_pair_data:
            logger.error(f"Missing pair data for last date {last_date}")
            next_day_pairs[-1] = "None"
        else:
            try:
                top_buy_pair = buy_pair_data[0] if isinstance(buy_pair_data, tuple) else (0, 0)
                top_short_pair = short_pair_data[0] if isinstance(short_pair_data, tuple) else (0, 0)
                buy_capture = buy_pair_data[1] if isinstance(buy_pair_data, tuple) else 0
                short_capture = short_pair_data[1] if isinstance(short_pair_data, tuple) else 0

                if not isinstance(top_buy_pair, tuple) or not isinstance(top_short_pair, tuple):
                    logger.error(f"Invalid pair format for {last_date}")
                    next_day_pairs[-1] = "None"
                else:
                    try:
                        # Calculate signals for the last date
                        buy_signal = df[f'SMA_{top_buy_pair[0]}'].loc[last_date] > df[f'SMA_{top_buy_pair[1]}'].loc[last_date]
                        short_signal = df[f'SMA_{top_short_pair[0]}'].loc[last_date] < df[f'SMA_{top_short_pair[1]}'].loc[last_date]
                        
                        if buy_signal and short_signal:
                            # Compare captures to determine which signal to use
                            if buy_capture > short_capture:
                                next_day_pairs[-1] = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
                            else:
                                next_day_pairs[-1] = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
                        elif buy_signal:
                            next_day_pairs[-1] = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
                        elif short_signal:
                            next_day_pairs[-1] = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
                        else:
                            next_day_pairs[-1] = "None"
                    except Exception as e:
                        logger.error(f"Error calculating signals: {str(e)}")
                        next_day_pairs[-1] = "None"
            except Exception as e:
                logger.error(f"Error processing pair data: {str(e)}")
                next_day_pairs[-1] = "None"

        # Add a transparent trace for hover information
        hover_text = [
            f"Current: {pair}<br>Capture: {cap:.2f}%<br>Next: {next_pair}"
            for pair, cap, next_pair in zip(active_pairs, cumulative_combined_captures, next_day_pairs)
        ]

        fig.add_trace(go.Scatter(
            x=cumulative_combined_captures.index,
            y=cumulative_combined_captures,
            mode='lines',
            line=dict(color='rgba(0,0,0,0)', width=0),
            showlegend=False,
            hovertext=hover_text,
            hoverinfo='text+x'
        ))

        # Add annotations for pair changes
        annotations = []
        last_pair = None
        for i, (date, color) in enumerate(zip(cumulative_combined_captures.index, colors)):
            pair = 'Buy' if color == 'green' else 'Short' if color == 'red' else 'Cash'

            if i == 0 or pair != last_pair:
                annotations.append(dict(
                    x=date,
                    y=cumulative_combined_captures.iloc[i],
                    text=pair,
                    showarrow=True,
                    arrowhead=2,
                    arrowsize=1,
                    arrowwidth=2,
                    arrowcolor="white",
                    font=dict(size=10, color="white"),
                    align="center",
                    ax=0,
                    ay=-40
                ))
            last_pair = pair

        # Only add annotations if the toggle is on
        if show_annotations:
            fig.update_layout(annotations=annotations)
        else:
            fig.update_layout(annotations=[])

        fig.update_layout(
            title=dict(
                text=f'{ticker.upper()} Color-Coded Cumulative Combined Capture Chart',
                font=dict(color='#80ff00')
            ),
            xaxis_title='Trading Day',
            yaxis_title='Cumulative Combined Capture (%)',
            hovermode='x unified',
            template='plotly_dark',
            showlegend=False,
            font=dict(color='#80ff00'),
            plot_bgcolor='black',
            paper_bgcolor='black',
            xaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            ),
            yaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            )
        )

        return fig

    except Exception as e:
        logger.error(f"Error in update_historical_top_pairs_chart: {str(e)}")
        logger.error(traceback.format_exc())
        return no_update  # Do not update the chart in case of error

@app.callback(
    [Output('most-productive-buy-pair', 'children'),
     Output('most-productive-short-pair', 'children'),
     Output('avg-capture-buy-leader', 'children'),
     Output('total-capture-buy-leader', 'children'),
     Output('avg-capture-short-leader', 'children'),
     Output('total-capture-short-leader', 'children'),
     Output('trading-direction', 'children'),
     Output('performance-expectation', 'children'),
     Output('confidence-percentage', 'children'),
     Output('trading-recommendations', 'children')],
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')]
)
def update_dynamic_strategy_display(ticker, n_intervals):
    if not ticker:
        return ["Please enter a ticker symbol."] * 10

    results = load_precomputed_results(ticker)
    
    if results is None:
        return ["Data not available. Please wait..."] * 10
    
    if 'status' in results:
        if results['status'] == 'processing':
            return ["Data is currently being processed."] * 10
        elif results['status'] == 'complete':
            if 'top_buy_pair' not in results or 'top_short_pair' not in results:
                return ["Processing complete, but top pairs not found. Please check data integrity."] * 10
        elif results['status'] == 'failed':
            return [f"Processing failed for {ticker}. Please check the error message."] * 10

    top_buy_pair = results.get('top_buy_pair')
    top_short_pair = results.get('top_short_pair')
    
    if top_buy_pair is None or top_short_pair is None:
        logger.warning(f"Missing top pairs data for {ticker}")
        return [no_update] + ["Data integrity issue - missing top pairs"] * 9

    df = results.get('preprocessed_data')
    if df is None or df.empty:
        logger.warning(f"Missing preprocessed data for {ticker}")
        return [no_update] + ["Data integrity issue - missing preprocessed data"] * 9

    # Validate top pairs format
    if not isinstance(top_buy_pair, tuple) or not isinstance(top_short_pair, tuple):
        logger.warning(f"Invalid top pairs format for {ticker}")
        return [no_update] + ["Data integrity issue - invalid pair format"] * 9

    try:
        # Validate top pairs data
        if not all(isinstance(pair, tuple) and len(pair) == 2 for pair in [top_buy_pair, top_short_pair]):
            logger.error(f"Invalid pair format detected for {ticker}")
            return [no_update] + ["Invalid pair format detected. Please reprocess data."] * 9

        # Validate that all required SMA columns exist
        required_smas = [
            f'SMA_{top_buy_pair[0]}', f'SMA_{top_buy_pair[1]}',
            f'SMA_{top_short_pair[0]}', f'SMA_{top_short_pair[1]}'
        ]
        
        missing_smas = [sma for sma in required_smas if sma not in df.columns]
        if missing_smas:
            logger.error(f"Missing SMA columns for {ticker}: {missing_smas}")
            return [no_update] + ["Missing required SMA columns. Please reprocess data."] * 9

        sma1_buy_leader = df[f'SMA_{top_buy_pair[0]}']
        sma2_buy_leader = df[f'SMA_{top_buy_pair[1]}']
        buy_signals_leader = sma1_buy_leader > sma2_buy_leader
        close_pct_change = df['Close'].pct_change().values

        sma1_short_leader = df[f'SMA_{top_short_pair[0]}']
        sma2_short_leader = df[f'SMA_{top_short_pair[1]}']
        short_signals_leader = sma1_short_leader < sma2_short_leader

    except KeyError:
        logger.error(f"Required SMA columns not found in the DataFrame for {ticker}")
        return [no_update] + ["Data not available or processing not yet complete. Please wait..."] * 9

    current_date = df.index[-1]
    previous_date = df.index[-2]

    def predict_signal(close_price):
        # Create a copy of the Close series with the new close_price
        close_series = df['Close'].copy()
        close_series.iloc[-1] = close_price
        
        # Recalculate the SMAs with the new close_price
        sma1_buy = close_series.rolling(window=top_buy_pair[0]).mean()
        sma2_buy = close_series.rolling(window=top_buy_pair[1]).mean()
        sma1_short = close_series.rolling(window=top_short_pair[0]).mean()
        sma2_short = close_series.rolling(window=top_short_pair[1]).mean()
        
        # Get the last SMA values
        sma1_buy_last = sma1_buy.iloc[-1]
        sma2_buy_last = sma2_buy.iloc[-1]
        sma1_short_last = sma1_short.iloc[-1]
        sma2_short_last = sma2_short.iloc[-1]
        
        # Determine signals
        buy_signal = sma1_buy_last > sma2_buy_last
        short_signal = sma1_short_last < sma2_short_last
        
        if buy_signal and not short_signal:
            return "Buy", f"SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]}"
        elif short_signal and not buy_signal:
            return "Short", f"SMA {top_short_pair[0]} / SMA {top_short_pair[1]}"
        elif buy_signal and short_signal:
            # Both signals active, decide based on capture
            if buy_capture > short_capture:
                return "Buy", f"SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]}"
            else:
                return "Short", f"SMA {top_short_pair[0]} / SMA {top_short_pair[1]}"
        else:
            return "Cash", "N/A"

    # Validate dates exist in the index
    if previous_date not in df.index or current_date not in df.index:
        logger.error(f"Missing required dates in data: prev={previous_date}, current={current_date}")
        return [no_update] + ["Missing required dates in data. Please reprocess data."] * 9

    try:
        # Calculate signals for today based on yesterday's close
        buy_signal = (sma1_buy_leader.loc[previous_date] > sma2_buy_leader.loc[previous_date]) if all(
            pd.notna([sma1_buy_leader.loc[previous_date], sma2_buy_leader.loc[previous_date]])) else False
        short_signal = (sma1_short_leader.loc[previous_date] < sma2_short_leader.loc[previous_date]) if all(
            pd.notna([sma1_short_leader.loc[previous_date], sma2_short_leader.loc[previous_date]])) else False

        # Calculate signals for tomorrow based on today's close
        next_buy_signal = (sma1_buy_leader.loc[current_date] > sma2_buy_leader.loc[current_date]) if all(
            pd.notna([sma1_buy_leader.loc[current_date], sma2_buy_leader.loc[current_date]])) else False
        next_short_signal = (sma1_short_leader.loc[current_date] < sma2_short_leader.loc[current_date]) if all(
            pd.notna([sma1_short_leader.loc[current_date], sma2_short_leader.loc[current_date]])) else False
    except Exception as e:
        logger.error(f"Error calculating signals: {str(e)}")
        return [no_update] + ["Error calculating signals. Please check the data."] * 9

    # Determine the current trading signal type
    if buy_signal and not short_signal:
        trading_signal_type = "Buy"
    elif short_signal and not buy_signal:
        trading_signal_type = "Short"
    else:
        trading_signal_type = "Cash (No active triggers)"

    trading_signal = f"Current Trading Signal ({current_date.strftime('%Y-%m-%d')}): {trading_signal_type}"

    # Determine the next trading signal type
    if next_buy_signal and not next_short_signal:
        next_trading_signal_type = "Buy"
    elif next_short_signal and not next_buy_signal:
        next_trading_signal_type = "Short"
    else:
        next_trading_signal_type = "Cash (No active triggers)"

    next_trading_day = current_date + pd.Timedelta(days=1)
    next_trading_signal = f"Next Trading Signal ({next_trading_day.strftime('%Y-%m-%d')}): {next_trading_signal_type}"

    most_productive_buy_pair_text = f"Most Productive Buy Pair: SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]}"
    most_productive_short_pair_text = f"Most Productive Short Pair: SMA {top_short_pair[0]} / SMA {top_short_pair[1]}"

    # Buy metrics
    buy_signals_shifted = buy_signals_leader.shift(1, fill_value=False)
    buy_returns_on_trigger_days = close_pct_change[buy_signals_shifted]
    buy_trigger_days = np.sum(buy_signals_shifted)
    buy_wins = np.sum(buy_returns_on_trigger_days > 0)
    buy_losses = np.sum(buy_returns_on_trigger_days <= 0)
    buy_win_ratio = buy_wins / buy_trigger_days if buy_trigger_days > 0 else 0
    avg_capture_buy = np.mean(buy_returns_on_trigger_days * 100) if buy_trigger_days > 0 else 0
    buy_capture = np.sum(buy_returns_on_trigger_days * 100) if buy_trigger_days > 0 else 0

    # Short metrics
    short_signals_shifted = short_signals_leader.shift(1, fill_value=False)
    short_returns_on_trigger_days = -close_pct_change[short_signals_shifted]
    short_trigger_days = np.sum(short_signals_shifted)
    short_wins = np.sum(short_returns_on_trigger_days > 0)
    short_losses = np.sum(short_returns_on_trigger_days <= 0)
    short_win_ratio = short_wins / short_trigger_days if short_trigger_days > 0 else 0
    avg_capture_short = np.mean(short_returns_on_trigger_days * 100) if short_trigger_days > 0 else 0
    short_capture = np.sum(short_returns_on_trigger_days * 100) if short_trigger_days > 0 else 0

    avg_capture_buy_leader = (
        f"Avg. Daily Capture % for Buy Leader: {avg_capture_buy:.4f}% "
        f"(Trigger Days: {buy_trigger_days}, Wins: {buy_wins}, Losses: {buy_losses}, Win Ratio: {buy_win_ratio * 100:.2f}%)"
    )

    avg_capture_short_leader = (
        f"Avg. Daily Capture % for Short Leader: {avg_capture_short:.4f}% "
        f"(Trigger Days: {short_trigger_days}, Wins: {short_wins}, Losses: {short_losses}, Win Ratio: {short_win_ratio * 100:.2f}%)"
    )

    total_capture_buy_leader = f"Total Capture for Buy Leader: {buy_capture:.4f}%"
    total_capture_short_leader = f"Total Capture for Short Leader: {short_capture:.4f}%"

    # Recalculate the dynamic cumulative performance for combined strategy
    daily_top_buy_pairs = results.get('daily_top_buy_pairs', {})
    daily_top_short_pairs = results.get('daily_top_short_pairs', {})

    dates = sorted(set(daily_top_buy_pairs.keys()) & set(daily_top_short_pairs.keys()))
    if not dates:
        total_capture = 0
        avg_daily_capture = 0
        trigger_days = 0
        wins = 0
        losses = 0
        win_ratio = 0
        std_dev = 0
        t_statistic = None
        p_value = None
        sharpe_ratio = 0
    else:
        daily_returns_series = df['Close'].pct_change().fillna(0)
        cumulative_captures = []
        current_capture = 0
        active_signals = []

        for i in range(1, len(dates)):
            prev_day = dates[i-1]
            current_day = dates[i]

            prev_buy_pair, prev_buy_cap = daily_top_buy_pairs[prev_day]
            prev_short_pair, prev_short_cap = daily_top_short_pairs[prev_day]

            if (prev_buy_pair != (0,0)) and (prev_short_pair != (0,0)):
                buy_signal = df[f'SMA_{prev_buy_pair[0]}'].loc[prev_day] > df[f'SMA_{prev_buy_pair[1]}'].loc[prev_day]
                short_signal = df[f'SMA_{prev_short_pair[0]}'].loc[prev_day] < df[f'SMA_{prev_short_pair[1]}'].loc[prev_day]

                if buy_signal and short_signal:
                    if prev_buy_cap > prev_short_cap:
                        current_position = 'Buy'
                    else:
                        current_position = 'Short'
                elif buy_signal:
                    current_position = 'Buy'
                elif short_signal:
                    current_position = 'Short'
                else:
                    current_position = 'None'
            elif (prev_buy_pair != (0,0)):
                buy_signal = df[f'SMA_{prev_buy_pair[0]}'].loc[prev_day] > df[f'SMA_{prev_buy_pair[1]}'].loc[prev_day]
                current_position = 'Buy' if buy_signal else 'None'
            elif (prev_short_pair != (0,0)):
                short_signal = df[f'SMA_{prev_short_pair[0]}'].loc[prev_day] < df[f'SMA_{prev_short_pair[1]}'].loc[prev_day]
                current_position = 'Short' if short_signal else 'None'
            else:
                current_position = 'None'

            daily_return = daily_returns_series.loc[current_day]
            if current_position == 'Buy':
                daily_capture = daily_return * 100
            elif current_position == 'Short':
                daily_capture = -daily_return * 100
            else:
                daily_capture = 0

            current_capture += daily_capture
            cumulative_captures.append(daily_capture)
            active_signals.append(current_position)

        if len(cumulative_captures) > 0:
            # Create signal mask excluding first day (to match the shifted signals approach)
            trigger_mask = [sig in ('Buy', 'Short') for sig in active_signals]
            trigger_days = sum(trigger_mask)

            # Extract signal_captures only for triggered days
            signal_captures = np.array([
                cap for cap, active_sig in zip(cumulative_captures, active_signals)
                if active_sig in ('Buy', 'Short')
            ])

            if signal_captures.size > 0:
                wins = np.sum(signal_captures > 0)
                losses = trigger_days - wins  # Ensure wins + losses equals trigger days
                win_ratio = (wins / trigger_days * 100) if trigger_days > 0 else 0.0
                avg_daily_capture = signal_captures.mean() if trigger_days > 0 else 0.0
                total_capture = signal_captures.sum() if trigger_days > 0 else 0.0

                # Calculate standard deviation using ddof=1 for sample standard deviation
                if trigger_days > 1:
                    std_dev = np.std(signal_captures, ddof=1)
                else:
                    std_dev = 0.0
            else:
                wins = losses = 0
                win_ratio = avg_daily_capture = total_capture = std_dev = 0.0

            # t-Statistic & p-value
            if trigger_days > 1 and std_dev != 0:
                t_statistic = avg_daily_capture / (std_dev / np.sqrt(trigger_days))
                degrees_of_freedom = trigger_days - 1
                p_value = 2 * (1 - stats.t.cdf(abs(t_statistic), df=degrees_of_freedom))

                confidence_levels = {
                    '90%': p_value < 0.10,
                    '95%': p_value < 0.05,
                    '99%': p_value < 0.01
                }
                log_subsection("Statistical Significance Analysis")
                log_metric("t-Statistic", f"{t_statistic:.4f}")
                log_metric("p-Value", f"{p_value:.4f}")
                log_metric("Degrees of Freedom", degrees_of_freedom)
                logger.info("")
                logger.info(f"{Colors.CYAN}Confidence Levels:{Colors.ENDC}")
                for level, significant in confidence_levels.items():
                    status = 'Significant' if significant else 'Not Significant'
                    color = Colors.BRIGHT_GREEN if significant else Colors.ORANGE
                    logger.info(f"  {Colors.OKBLUE}{level} Confidence:{Colors.ENDC} {color}{status}{Colors.ENDC}")
            else:
                t_statistic = None
                p_value = None
                logger.info("\nStatistical Significance Analysis:")
                logger.info("Insufficient data to perform statistical significance analysis.\n")

            # Annualized Sharpe Ratio logic consistent with other sections
            risk_free_rate = 5.0
            if trigger_days > 1 and std_dev != 0:
                annualized_return = avg_daily_capture * 252
                annualized_std = std_dev * np.sqrt(252)
                sharpe_ratio = (annualized_return - risk_free_rate) / annualized_std
            else:
                sharpe_ratio = 0.0

        else:
            # No captures at all
            total_capture = 0.0
            avg_daily_capture = 0.0
            trigger_days = 0
            wins = 0
            losses = 0
            win_ratio = 0.0
            std_dev = 0.0
            t_statistic = None
            p_value = None
            sharpe_ratio = 0.0

    if next_trading_signal_type == "Buy":
        active_returns = buy_returns_on_trigger_days
    elif next_trading_signal_type == "Short":
        active_returns = short_returns_on_trigger_days
    else:
        active_returns = np.array([])

    active_trigger_days = len(active_returns)
    if active_trigger_days > 0:
        performance_expectation = np.mean(active_returns)
        active_wins = np.sum(active_returns > 0)
        active_losses = np.sum(active_returns <= 0)
        active_win_ratio = active_wins / active_trigger_days
        performance_expectation_text = (
            f"Next Signal Performance Expectation: {performance_expectation * 100:.4f}% "
            f"(Historical Trigger Days: {active_trigger_days}, Wins: {active_wins}, Losses: {active_losses}, Win Ratio: {active_win_ratio * 100:.2f}%)"
        )
        confidence_percentage_text = f"Historical Win Ratio for Next Signal: {active_win_ratio * 100:.2f}%"
    else:
        performance_expectation_text = "Next Signal Performance Expectation: N/A (No historical triggers)"
        confidence_percentage_text = "Historical Win Ratio for Next Signal: N/A (No historical triggers)"

    def find_crossing_price(n1, n2):
        if n1 == n2:
            return None
        min_length = max(n1, n2)
        if len(df) < min_length:
            return None
        sum1 = df['Close'].iloc[-(n1):-1].sum()
        sum2 = df['Close'].iloc[-(n2):-1].sum()
        numerator = n1 * sum2 - n2 * sum1
        denominator = n2 - n1
        if denominator == 0:
            return None
        crossing_price = numerator / denominator
        return crossing_price if crossing_price > 0 and np.isfinite(crossing_price) else None

    crossing_price_buy = find_crossing_price(top_buy_pair[0], top_buy_pair[1])
    crossing_price_short = find_crossing_price(top_short_pair[0], top_short_pair[1])

    current_price = df['Close'].iloc[-1]
    max_price = current_price * 1.5
    price_points = []
    if crossing_price_buy is not None and crossing_price_buy > 0:
        price_points.append(crossing_price_buy)
    if crossing_price_short is not None and crossing_price_short > 0:
        price_points.append(crossing_price_short)
    price_points.append(current_price)
    price_points = sorted(set(price_points))
    if 0 not in price_points:
        price_points.insert(0, 0)
    price_points.append(max_price)

    price_ranges = []
    for i in range(len(price_points) - 1):
        low = price_points[i]
        high = price_points[i + 1]
        if high > low:
            price_ranges.append({'low': low, 'high': high})
    if price_points[-1] < float('inf'):
        price_ranges.append({'low': price_points[-1], 'high': float('inf')})

    predictions = []
    for pr in price_ranges:
        low = pr['low']
        high = pr['high']
        sample_price = low + (high - low) * 0.01 if high != float('inf') else low * 1.01
        signal, active_pair = predict_signal(sample_price)
        recommendations = {
            'Buy': 'Enter Buy',
            'Short': 'Enter Short',
            'Cash': 'Hold Cash'
        }
        recommendation = recommendations.get(signal, 'Hold Cash')
        price_range_str = f"${low:.2f} - ${high:.2f}" if high != float('inf') else f"${low:.2f} and above"
        if signal in ['Buy', 'Short']:
            signal_display = f"{signal} ({top_buy_pair[0]},{top_buy_pair[1]})" if signal == 'Buy' else f"{signal} ({top_short_pair[0]},{top_short_pair[1]})"
        else:
            signal_display = signal
            
        predictions.append({
            'price_range': price_range_str,
            'signal': signal_display,
            'active_pair': active_pair,
            'recommendation': recommendation
        })

    logger.info("")  # Line break before section
    log_section("Forecast Recommendations")
    for pred in predictions:
        logger.info(f"  💵 {pred['price_range']:<20} → {pred['signal']:<12} [{pred['recommendation']}]")
    logger.info("")  # Clean line break

    trading_recommendations = [
        html.Div([
            html.H2("Dynamic Master Trading Strategy", className="mb-4"),
            
            html.Div([
                html.H4("1. Summary of Top Performing Pairs", className="mb-3"),
                html.P(f"{most_productive_buy_pair_text} (Total Capture: {buy_capture:.4f}%)", className="mb-2"),
                html.P(f"{most_productive_short_pair_text} (Total Capture: {short_capture:.4f}%)", className="mb-2"),
            ], className="mb-4"),
            
            html.Div([
                html.H4("2. Current Top Performing Pair Metrics", className="mb-3"),
                html.H5("Buy Leader Performance:", className="mb-2"),
                html.P(f"Average Daily Capture (%): {avg_capture_buy:.4f}%", className="mb-1"),
                html.P(f"Total Capture (%): {buy_capture:.4f}%", className="mb-1"),
                html.P(f"Trigger Days: {int(buy_trigger_days):,}", className="mb-1"),
                html.P(f"Wins: {int(buy_wins):,}", className="mb-1"),
                html.P(f"Losses: {int(buy_losses):,}", className="mb-1"),
                html.P(f"Win Ratio: {buy_win_ratio * 100:.2f}%", className="mb-3"),
                
                html.H5("Short Leader Performance:", className="mb-2"),
                html.P(f"Average Daily Capture (%): {avg_capture_short:.4f}%", className="mb-1"),
                html.P(f"Total Capture (%): {short_capture:.4f}%", className="mb-1"),
                html.P(f"Trigger Days: {int(short_trigger_days):,}", className="mb-1"),
                html.P(f"Wins: {int(short_wins):,}", className="mb-1"),
                html.P(f"Losses: {int(short_losses):,}", className="mb-1"),
                html.P(f"Win Ratio: {short_win_ratio * 100:.2f}%", className="mb-1"),
            ], className="mb-4"),
            
            html.Div([
                html.H4("3. Trading Signals", className="mb-3"),
                html.P(
                    f"Current Trading Signal ({current_date.strftime('%Y-%m-%d')}): {trading_signal_type} "
                    f"(SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]})" 
                    if trading_signal_type == "Buy" else 
                    f"Current Trading Signal ({current_date.strftime('%Y-%m-%d')}): {trading_signal_type} "
                    f"(SMA {top_short_pair[0]} / SMA {top_short_pair[1]})",
                    className="mb-2"
                ),
                html.P(
                    f"Next Trading Signal ({next_trading_day.strftime('%Y-%m-%d')}): {next_trading_signal_type} "
                    f"(SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]})" 
                    if next_trading_signal_type == "Buy" else 
                    f"Next Trading Signal ({next_trading_day.strftime('%Y-%m-%d')}): {next_trading_signal_type} "
                    f"(SMA {top_short_pair[0]} / SMA {top_short_pair[1]})",
                    className="mb-2"
                ),
            ], className="mb-4"),
            
            html.Div([
                html.H4("4. Combined Strategy Performance", className="mb-3"),
                html.P(f"Total Capture (%): {total_capture:.4f}%", className="mb-1"),
                html.P(f"Average Daily Capture (%): {avg_daily_capture:.4f}%", className="mb-1"),
                html.P(f"Daily Standard Deviation (%): {std_dev:.4f}%", className="mb-1"),
                html.P(f"Annualized Sharpe Ratio: {sharpe_ratio:.2f}", className="mb-1"),
                html.Div([
                    html.H5("Statistical Significance Analysis:", className="mb-2"),
                    html.P(f"t-Statistic: {t_statistic:.4f}" if t_statistic is not None else "t-Statistic: N/A", className="mb-1"),
                    html.P(f"p-Value: {p_value:.4f}" if p_value is not None else "p-Value: N/A", className="mb-1"),
                    html.P("Confidence Levels:", className="mb-1"),
                    html.Ul([
                        html.Li(f"90% Confidence: {'Significant' if p_value is not None and p_value < 0.10 else 'Not Significant'}", 
                            style={'color': 'green' if p_value is not None and p_value < 0.10 else 'red'}),
                        html.Li(f"95% Confidence: {'Significant' if p_value is not None and p_value < 0.05 else 'Not Significant'}", 
                            style={'color': 'green' if p_value is not None and p_value < 0.05 else 'red'}),
                        html.Li(f"99% Confidence: {'Significant' if p_value is not None and p_value < 0.01 else 'Not Significant'}", 
                            style={'color': 'green' if p_value is not None and p_value < 0.01 else 'red'}),
                    ], className="mb-2"),
                ], className="mb-3"),
                # Use trigger_days, wins, losses directly
                html.P(f"Trigger Days: {trigger_days:,}", className="mb-1"),
                html.P(f"Wins: {wins:,}", className="mb-1"),
                html.P(f"Losses: {losses:,}", className="mb-1"),
                html.P(f"Win Ratio: {win_ratio:.2f}%", className="mb-1"),
            ], className="mb-4"),
            
            html.Div([
                html.H4("5. Trading Recommendations", className="mb-3"),
                html.H5(f"For Current Trading Session ({current_date.strftime('%Y-%m-%d')}):", className="mb-2"),
                html.P(f"Leading Buy SMA Pair: SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]} (Total Capture: {buy_capture:.4f}%)", className="mb-1"),
                html.P(f"Leading Short SMA Pair: SMA {top_short_pair[0]} / SMA {top_short_pair[1]} (Total Capture: {short_capture:.4f}%)", className="mb-1"),
                html.P(f"Current Buy Signal: {'TRUE' if buy_signal else 'FALSE'}", className="mb-1"),
                html.P(f"Current Short Signal: {'TRUE' if short_signal else 'FALSE'}", className="mb-1"),
                html.P(f"Recommendation: {'Enter Short Position' if short_signal else 'Enter Buy Position' if buy_signal else 'Hold Cash Position'}", className="mb-3"),
                
                html.H5(f"For Next Trading Session ({next_trading_day.strftime('%Y-%m-%d')}):", className="mb-2"),
                html.P(f"Next Buy Signal: {'TRUE' if next_buy_signal else 'FALSE'}", className="mb-1"),
                html.P(f"Next Short Signal: {'TRUE' if next_short_signal else 'FALSE'}", className="mb-1"),
                html.P(f"Recommendation: {'Enter Buy Position' if next_buy_signal else 'Enter Short Position' if next_short_signal else 'Hold Cash Position'} before EOD ({current_date.strftime('%Y-%m-%d')})", className="mb-1"),
            ], className="mb-4"),
            
            html.Div([
                html.H4("6. Forecast Recommendations", className="mb-3"),
                html.P(f"Recommendations for IMMEDIATELY BEFORE EOD on ({next_trading_day.strftime('%Y-%m-%d')}):", className="mb-2"),
                html.Table([
                    html.Thead(html.Tr([html.Th("Price Range"), html.Th("Predicted Signal"), html.Th("Active Pair"), html.Th("Recommendation")])),
                    html.Tbody([
                        html.Tr([
                            html.Td(prediction['price_range']),
                            html.Td(prediction['signal']),
                            html.Td(prediction['active_pair']),
                            html.Td(prediction['recommendation'])
                        ]) for prediction in predictions
                    ])
                ], className="table table-striped table-bordered")
            ], className="mb-4"),
            
        ], className="p-4 bg-light rounded")
    ]

    # After Forecast Recommendations are complete, update results
    results['last_recommendation_time'] = time.time()
    save_precomputed_results(ticker, results)

    return (
        most_productive_buy_pair_text,
        most_productive_short_pair_text,
        avg_capture_buy_leader,
        total_capture_buy_leader,
        avg_capture_short_leader,
        total_capture_short_leader,
        trading_signal,
        performance_expectation_text,
        confidence_percentage_text,
        html.Div(trading_recommendations)
    )

@app.callback(
    [Output('chart', 'figure'),
     Output('trigger-days-buy', 'children'),
     Output('win-ratio-buy', 'children'),
     Output('avg-daily-capture-buy', 'children'),
     Output('total-capture-buy', 'children'),
     Output('trigger-days-short', 'children'),
     Output('win-ratio-short', 'children'),
     Output('avg-daily-capture-short', 'children'),
     Output('total-capture-short', 'children'),
     Output('buy-pair-header', 'children'),
     Output('short-pair-header', 'children')],
    [Input('ticker-input', 'value'),
     Input('sma-input-1', 'value'),
     Input('sma-input-2', 'value'),
     Input('sma-input-3', 'value'),
     Input('sma-input-4', 'value')]
)
def update_chart(ticker, sma_day_1, sma_day_2, sma_day_3, sma_day_4):
    if ticker is None:
        empty_fig = go.Figure()
        empty_fig.update_layout(
            plot_bgcolor='black',
            paper_bgcolor='black',
            font=dict(color='#80ff00'),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            template='plotly_dark'
        )
        return empty_fig, '', '', '', '', '', '', '', '', 'Buy Pair Results', 'Short Pair Results'

    df = fetch_data(ticker)
    if df is None or df.empty:
        empty_fig = go.Figure()
        empty_fig.update_layout(
            title=dict(
                text=f"No data available for {ticker}",
                font=dict(color='#80ff00')
            ),
            plot_bgcolor='black',
            paper_bgcolor='black',
            font=dict(color='#80ff00'),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            template='plotly_dark'
        )
        return empty_fig, '', '', '', '', '', '', '', '', 'Buy Pair Results', 'Short Pair Results'
        
    # Create base figure with just the price chart
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name=f'{ticker} Close'))
    
    # If any SMA inputs are missing, return just the price chart
    if any(sma_day is None for sma_day in [sma_day_1, sma_day_2, sma_day_3, sma_day_4]):
        fig.update_layout(
            title=dict(
                text=f'{ticker.upper()} Closing Prices',
                font=dict(color='#80ff00')
            ),
            xaxis_title='Trading Day',
            yaxis_title=f'{ticker.upper()} Closing Price',
            template='plotly_dark',
            font=dict(color='#80ff00'),
            plot_bgcolor='black',
            paper_bgcolor='black',
            xaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            ),
            yaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            )
        )
        return fig, '', '', '', '', '', '', '', '', 'Buy Pair Results', 'Short Pair Results'

    min_date = df.index.min()
    max_date = df.index.max()
    start_date = min_date.strftime('%Y-%m-%d') if pd.notnull(min_date) else 'No date available'
    last_date = max_date.strftime('%Y-%m-%d') if pd.notnull(max_date) else 'No date available'

    # Calculate SMAs based on user input
    sma1_buy = df['Close'].rolling(window=sma_day_1).mean()
    sma2_buy = df['Close'].rolling(window=sma_day_2).mean()
    sma1_short = df['Close'].rolling(window=sma_day_3).mean()
    sma2_short = df['Close'].rolling(window=sma_day_4).mean()

    buy_signals = sma1_buy > sma2_buy
    short_signals = sma1_short < sma2_short

    daily_returns = df['Close'].pct_change()

    # Shift signals to align with next day's returns
    buy_signals_shifted = buy_signals.shift(1, fill_value=False)
    short_signals_shifted = short_signals.shift(1, fill_value=False)

    # Calculate Buy returns on days when Buy signal was active
    buy_returns_on_trigger_days = daily_returns[buy_signals_shifted]
    buy_trigger_days = buy_signals_shifted.sum()
    buy_wins = (buy_returns_on_trigger_days > 0).sum()
    buy_losses = (buy_returns_on_trigger_days <= 0).sum()
    buy_win_ratio = buy_wins / buy_trigger_days if buy_trigger_days > 0 else 0
    buy_total_capture = buy_returns_on_trigger_days.sum() * 100 if buy_trigger_days > 0 else 0  # Convert to percentage
    buy_avg_daily_capture = buy_total_capture / buy_trigger_days if buy_trigger_days > 0 else 0

    # Calculate Short returns on days when Short signal was active
    short_returns_on_trigger_days = -daily_returns[short_signals_shifted]
    short_trigger_days = short_signals_shifted.sum()
    short_wins = (short_returns_on_trigger_days > 0).sum()
    short_losses = (short_returns_on_trigger_days <= 0).sum()
    short_win_ratio = short_wins / short_trigger_days if short_trigger_days > 0 else 0
    short_total_capture = short_returns_on_trigger_days.sum() * 100 if short_trigger_days > 0 else 0  # Convert to percentage
    short_avg_daily_capture = short_total_capture / short_trigger_days if short_trigger_days > 0 else 0

    # Prepare detailed strings for display
    trigger_days_buy = f"Buy Trigger Days: {int(buy_trigger_days)}"
    win_ratio_buy = (f"Buy Win Ratio: {buy_win_ratio * 100:.2f}% "
                    f"(Wins: {int(buy_wins)}, Losses: {int(buy_losses)}, "
                    f"Trigger Days: {int(buy_trigger_days)})")
    avg_daily_capture_buy = f"Buy Avg. Daily Capture: {buy_avg_daily_capture:.4f}%"
    total_capture_buy = f"Buy Total Capture: {buy_total_capture:.4f}%"

    trigger_days_short = f"Short Trigger Days: {int(short_trigger_days)}"
    win_ratio_short = (f"Short Win Ratio: {short_win_ratio * 100:.2f}% "
                    f"(Wins: {int(short_wins)}, Losses: {int(short_losses)}, "
                    f"Trigger Days: {int(short_trigger_days)})")
    avg_daily_capture_short = f"Short Avg. Daily Capture: {short_avg_daily_capture:.4f}%"
    total_capture_short = f"Short Total Capture: {short_total_capture:.4f}%"

    # Create the chart figure
    fig = go.Figure()

    # Add closing prices trace
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name=f'{ticker} Close'))

    # Add SMA traces
    fig.add_trace(go.Scatter(x=df.index, y=sma1_buy, mode='lines', name=f'SMA {sma_day_1} (Buy)', visible=True))
    fig.add_trace(go.Scatter(x=df.index, y=sma2_buy, mode='lines', name=f'SMA {sma_day_2} (Buy)', visible=True))
    fig.add_trace(go.Scatter(x=df.index, y=sma1_short, mode='lines', name=f'SMA {sma_day_3} (Short)', visible=True))
    fig.add_trace(go.Scatter(x=df.index, y=sma2_short, mode='lines', name=f'SMA {sma_day_4} (Short)', visible=True))

    # Calculate Buy returns over the full date range
    buy_returns_full = daily_returns.where(buy_signals_shifted, 0)
    short_returns_full = -daily_returns.where(short_signals_shifted, 0)

    # Calculate cumulative capture over the full date range
    total_buy_capture_full = buy_returns_full.cumsum() * 100  # Convert to percentage
    total_short_capture_full = short_returns_full.cumsum() * 100  # Convert to percentage

    # Add Total Buy Capture and Total Short Capture traces
    fig.add_trace(go.Scatter(x=total_buy_capture_full.index, y=total_buy_capture_full, mode='lines', name='Total Buy Capture'))
    fig.add_trace(go.Scatter(x=total_short_capture_full.index, y=total_short_capture_full, mode='lines', name='Total Short Capture'))

    # Customize layout
    fig.update_layout(
        title=dict(
            text=f'{ticker.upper()} Closing Prices, SMAs, and Total Capture (Start Date: {start_date}, Last Date: {last_date})',
            font=dict(color='#80ff00')
        ),
        xaxis_title='Trading Day',
        yaxis_title=f'{ticker.upper()} Closing Price',
        hovermode='x',
        uirevision='static',
        template='plotly_dark',
        font=dict(color='#80ff00'),
        plot_bgcolor='black',
        paper_bgcolor='black',
        xaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        ),
        yaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        )
    )

    # Create header labels with the actual pair values
    buy_pair_header = f"Buy Pair ({sma_day_1}, {sma_day_2}) Results" if sma_day_1 and sma_day_2 else "Buy Pair Results"
    short_pair_header = f"Short Pair ({sma_day_3}, {sma_day_4}) Results" if sma_day_3 and sma_day_4 else "Short Pair Results"
    
    return fig, trigger_days_buy, win_ratio_buy, avg_daily_capture_buy, total_capture_buy, trigger_days_short, win_ratio_short, avg_daily_capture_short, total_capture_short, buy_pair_header, short_pair_header

@app.callback(
    Output('update-interval', 'disabled'),
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')]
)
def disable_interval_when_data_loaded(ticker, n_intervals):
    if not ticker:
        return True  # Disable interval when no ticker is entered

    status = read_status(ticker)
    if status['status'] == 'complete' or status['status'] == 'failed':
        return True  # Disable interval once data is loaded or if processing failed
    else:
        return False  # Keep interval running while processing
    
# Removed duplicate print_timing_summary - using the one defined earlier

@app.callback(
    [Output("loading-spinner-output", "children"),
     Output('timing-summary-printed', 'data')],
    [Input('combined-capture-chart', 'figure'),
     Input('historical-top-pairs-chart', 'figure'),
     Input('chart', 'figure'),
     Input('ticker-input', 'value')],
    [State('timing-summary-printed', 'data')]
)
def update_output_and_reset(combined_capture, historical_top_pairs, chart, ticker, timing_summary_printed):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update

    trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]

    if trigger_id == 'ticker-input':
        # Reset the timing summary printed flag when ticker changes
        return no_update, False
    elif all([combined_capture, historical_top_pairs, chart]) and not timing_summary_printed:
        print_timing_summary(ticker)
        return "Charts loaded successfully", True
    else:
        return no_update, no_update

from dash import dash_table

@app.callback(
    [Output('secondary-capture-chart', 'figure'),
     Output('secondary-metrics-table', 'data'),
     Output('secondary-metrics-table', 'columns'),
     Output('secondary-ticker-input-feedback', 'children')],
    [Input('ticker-input', 'value'),
     Input('secondary-ticker-input', 'value'),
     Input('invert-signals-toggle', 'value'),
     Input('show-secondary-annotations-toggle', 'value'),
     Input('update-interval', 'n_intervals'),
     Input('trading-recommendations', 'children')],
    prevent_initial_call=True
)
def update_secondary_capture_chart(primary_ticker, secondary_tickers_input, invert_signals, show_annotations, n_intervals, trading_recommendations):
    empty_fig = go.Figure()
    empty_fig.update_layout(template='plotly_dark')

    if not primary_ticker or not secondary_tickers_input:
        return empty_fig, [], [], ''

    # Load and verify primary ticker results
    results = load_precomputed_results(primary_ticker)
    if not results:
        return empty_fig, [], [], 'Waiting for primary ticker data...'

    # Check for required data components
    required_keys = ['preprocessed_data', 'active_pairs', 'cumulative_combined_captures']
    if not all(key in results for key in required_keys):
        return empty_fig, [], [], 'Waiting for complete primary ticker analysis...'

    # Parse secondary tickers
    try:
        logger.info(f"\n{'-' * 80}")
        logger.info("INITIATING SECONDARY ANALYSIS")
        logger.info(f"Primary Ticker: {primary_ticker.upper()}")

        secondary_tickers = [ticker.strip().upper() for ticker in secondary_tickers_input.split(',') if ticker.strip()]
        if not secondary_tickers:
            return empty_fig, [], [], 'Please enter valid ticker symbols'

        # Remove duplicates while preserving order
        secondary_tickers = list(dict.fromkeys(secondary_tickers))
        logger.info(f"Processing secondary tickers: {', '.join(secondary_tickers)}")
        logger.info(f"{'-' * 80}\n")

        # Fetch secondary ticker data
        secondary_dfs = {}
        for ticker in secondary_tickers:
            df = fetch_data(ticker, is_secondary=True)
            if df is not None and not df.empty:
                secondary_dfs[ticker] = df
            else:
                logger.warning(f"Unable to fetch data for {ticker.upper()}")

        if not secondary_dfs:
            return empty_fig, [], [], 'No valid data available for secondary tickers'

        # Process signals
        active_pairs = results['active_pairs']
        cumulative_combined_captures = results['cumulative_combined_captures']
        dates = cumulative_combined_captures.index

        logger.info(f"Processing signals for {len(dates)} trading days")

        # Initialize containers
        fig = go.Figure()
        metrics_list = []

        # Process each secondary ticker
        for ticker, secondary_df in secondary_dfs.items():
            common_dates = dates.intersection(secondary_df.index)
            if len(common_dates) < 2:
                logger.warning(f"Insufficient data overlap for {ticker.upper()}")
                continue

            # Align signals and prices
            signals = pd.Series(active_pairs, index=dates).loc[common_dates]
            signals = signals.astype(str)
            prices = secondary_df['Close'].loc[common_dates]

            # Apply inversion if necessary
            if invert_signals:
                signals = signals.apply(
                    lambda x: 'Short' if x.startswith('Buy') else
                              'Buy' if x.startswith('Short') else x
                )

            # Process signals to extract 'Buy', 'Short', or 'None'
            signals = signals.apply(
                lambda x: 'Buy' if x.strip().startswith('Buy') else
                          'Short' if x.strip().startswith('Short') else 'None'
            )

            # Reindex signals and prices to a common index
            common_index = signals.index.union(prices.index)
            signals = signals.reindex(common_index).fillna('None')
            prices = prices.reindex(common_index).ffill()

            # Compute daily returns
            daily_returns = prices.pct_change().fillna(0)

            # Ensure signals and daily_returns have the same index
            signals = signals.loc[daily_returns.index]

            # Calculate captures
            buy_mask = signals == 'Buy'
            short_mask = signals == 'Short'

            daily_captures = pd.Series(0.0, index=signals.index)
            daily_captures[buy_mask] = daily_returns[buy_mask] * 100
            daily_captures[short_mask] = -daily_returns[short_mask] * 100

            cumulative_captures = daily_captures.cumsum()

            # Calculate metrics
            trigger_days = int((buy_mask | short_mask).sum())
            metrics = {'Ticker': ticker, 'Trigger Days': trigger_days}

            if trigger_days > 0:
                signal_captures = daily_captures[buy_mask | short_mask]
                wins = int((signal_captures > 0).sum())
                losses = trigger_days - wins
                win_ratio = round((wins / trigger_days * 100), 2) if trigger_days > 0 else 0.0

                # Compute raw (unrounded) values for the captures:
                raw_avg_daily = signal_captures.mean() if trigger_days > 0 else 0.0
                raw_total_capture = cumulative_captures.iloc[-1] if not cumulative_captures.empty else 0.0

                # Compute standard deviation with ddof=1 for sample std if we have more than 1 trigger day:
                raw_std_dev = signal_captures.std(ddof=1) if trigger_days > 1 else 0.0

                # Sharpe ratio logic (annualized), using raw values first:
                risk_free_rate = 5.0  # 5% annual
                annualized_return = raw_avg_daily * 252
                annualized_std = raw_std_dev * np.sqrt(252) if raw_std_dev > 0 else 0.0
                raw_sharpe = 0.0
                if annualized_std > 0:
                    raw_sharpe = (annualized_return - risk_free_rate) / annualized_std

                # Calculate t-stat and p-value with raw values:
                if trigger_days > 1 and raw_std_dev > 0:
                    t_statistic_val = raw_avg_daily / (raw_std_dev / np.sqrt(trigger_days))
                    dfreedom = trigger_days - 1
                    p_val = 2 * (1 - stats.t.cdf(abs(t_statistic_val), df=dfreedom))
                    # Now round:
                    t_statistic = round(t_statistic_val, 4)
                    p_value = round(p_val, 4)
                else:
                    t_statistic = None
                    p_value = None

                # Finally, round or format the metrics for display:
                avg_daily_capture = round(raw_avg_daily, 4)
                total_capture = round(raw_total_capture, 4)
                std_dev = round(raw_std_dev, 4)
                sharpe_ratio = round(raw_sharpe, 2)

                metrics.update({
                    'Wins': wins,
                    'Losses': losses,
                    'Win Ratio (%)': win_ratio,
                    'Std Dev (%)': std_dev,
                    'Sharpe Ratio': sharpe_ratio,
                    't-Statistic': t_statistic if t_statistic is not None else 'N/A',
                    'p-Value': p_value if p_value is not None else 'N/A',
                    'Significant 90%': 'Yes' if p_value is not None and p_value < 0.10 else 'No',
                    'Significant 95%': 'Yes' if p_value is not None and p_value < 0.05 else 'No',
                    'Significant 99%': 'Yes' if p_value is not None and p_value < 0.01 else 'No',
                    'Avg Daily Capture (%)': avg_daily_capture,
                    'Total Capture (%)': total_capture
                })
            else:
                metrics.update({
                    'Wins': 0,
                    'Losses': 0,
                    'Win Ratio (%)': 0.0,
                    'Avg Daily Capture (%)': 0.0,
                    'Total Capture (%)': 0.0
                })

            metrics_list.append(metrics)
            logger.info(f"Processed {ticker} - Capture: {metrics['Total Capture (%)']:.2f}%, "
                        f"Win Ratio: {metrics['Win Ratio (%)']:.2f}%, "
                        f"Days: {metrics['Trigger Days']}")

            # Add chart trace
            fig.add_trace(go.Scatter(
                x=cumulative_captures.index,
                y=cumulative_captures.values,
                mode='lines',
                name=ticker,
                line=dict(width=2),
                hovertemplate=(
                    "Ticker: " + ticker + "<br>" +
                    "Date: %{x}<br>" +
                    "Cumulative Capture: %{y:.2f}%<br>" +
                    "Signal: %{customdata}<br>" +
                    "<extra></extra>"
                ),
                customdata=signals.values
            ))

        if not metrics_list:
            return empty_fig, [], [], 'No valid data available for processing'

        # Prepare metrics table
        metrics_df = pd.DataFrame(metrics_list)
        metrics_df.sort_values(by='Avg Daily Capture (%)', ascending=False, inplace=True)
        columns = [{'name': col, 'id': col} for col in metrics_df.columns]
        data = metrics_df.to_dict('records')

        # Configure chart layout
        fig.update_layout(
            title=dict(
                text=f'{", ".join(secondary_dfs.keys())} Following {primary_ticker.upper()} {"(Inverted)" if invert_signals else ""} Signals',
                font=dict(color='#80ff00')
            ),
            xaxis_title='Date',
            yaxis_title='Cumulative Capture (%)',
            hovermode='x unified',
            template='plotly_dark',
            showlegend=True,
            font=dict(color='#80ff00'),
            plot_bgcolor='black',
            paper_bgcolor='black',
            xaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            ),
            yaxis=dict(
                color='#80ff00',
                showgrid=True,
                gridcolor='#80ff00',
                zerolinecolor='#80ff00',
                linecolor='#80ff00',
                tickfont=dict(color='#80ff00')
            )
        )

        # Add annotations if enabled
        if show_annotations:
            shapes = []
            annotations = []

            # Identify signal changes
            signal_changes = signals[signals != signals.shift(1)]
            for date, signal in signal_changes.iteritems():
                shapes.append(dict(
                    type="line",
                    xref="x",
                    yref="paper",
                    x0=date,
                    x1=date,
                    y0=0,
                    y1=1,
                    line=dict(
                        color="#80ff00",
                        width=1,
                        dash="dash"
                    ),
                    opacity=0.5
                ))

                annotations.append(dict(
                    x=date,
                    y=1,
                    xref="x",
                    yref="paper",
                    text=signal,
                    showarrow=False,
                    font=dict(
                        color="#80ff00",
                        size=10
                    ),
                    bgcolor="rgba(0,0,0,0.5)",
                    xanchor='left',
                    yanchor='top'
                ))

            fig.update_layout(shapes=shapes, annotations=annotations)

        return fig, data, columns, ''

    except Exception as e:
        logger.error(f"Error in secondary chart processing: {str(e)}")
        logger.error(traceback.format_exc())
        return empty_fig, [], [], f'Processing error: {str(e)}'

# Callback to add/remove primary ticker inputs dynamically and handle Combination clicks
@app.callback(
    Output('primary-tickers-container', 'children'),
    [Input('add-primary-button', 'n_clicks'),
     Input({'type': 'delete-primary-button', 'index': ALL}, 'n_clicks'),
     Input('optimization-results-table', 'active_cell')],
    [State('primary-tickers-container', 'children'),
     State('optimization-results-table', 'derived_virtual_data')],
    prevent_initial_call=True
)
def update_primary_tickers(add_click, delete_clicks, active_cell, children, virtual_data):
    ctx = dash.callback_context

    if not ctx.triggered:
        raise PreventUpdate

    triggered_prop = ctx.triggered[0]['prop_id'].split('.')
    triggered_id = triggered_prop[0]

    if triggered_id == 'add-primary-button':
        # Add a new primary ticker row
        if children is None:
            children = []
        new_index = len(children)
        new_ticker_row = create_primary_ticker_row(new_index)
        children.append(new_ticker_row)
        return children

    elif 'delete-primary-button' in triggered_id:
        # A delete button was clicked
        triggered_dict = ast.literal_eval(triggered_id)
        if 'index' not in triggered_dict:
            raise PreventUpdate

        delete_index = int(triggered_dict['index'])
        logger.info(f"Delete requested for index: {delete_index}")
        
        # Log current state before deletion
        current_indices = [child['props']['id']['index'] for child in children]
        logger.info(f"Current indices before deletion: {current_indices}")
        
        # Find the child to delete by matching the exact index
        child_to_delete = None
        for child in children:
            if child['props']['id']['index'] == delete_index:
                child_to_delete = child
                break
                
        if child_to_delete is None:
            logger.warning(f"Could not find child with index {delete_index}")
            raise PreventUpdate
            
        # Remove the specific child
        children.remove(child_to_delete)
        
        # Re-index the remaining children
        new_children = reindex_children(children)
        
        # Log state after reindexing
        new_indices = [child['props']['id']['index'] for child in new_children]
        logger.info(f"Indices after reindexing: {new_indices}")
        
        return new_children

    elif triggered_id == 'optimization-results-table':
        # Clear existing state before handling new combination
        if not active_cell or active_cell['column_id'] != 'Combination':
            raise PreventUpdate
        
        row = active_cell['row']
        if virtual_data is None or row is None or row >= len(virtual_data):
            raise PreventUpdate

        # Clear any existing children
        logger.info("Clearing existing primary ticker configuration")
        children = []
        
        combination_html = virtual_data[row]['Combination']

        # Parse the HTML content to extract tickers and their states
        soup = BeautifulSoup(combination_html, 'html.parser')
        tickers = []
        invert_values = []
        mute_values = []

        # Extract tickers and their states
        for span in soup.find_all('span'):
            ticker = span.text.strip()
            style = span.get('style', '')
            invert = False  # Default invert value
            if 'color:red' in style:
                invert = True
            elif 'color:#80ff00' in style or '#80ff00' in style:
                invert = False
            else:
                invert = False  # Default if color not matched
            tickers.append(ticker)
            invert_values.append(invert)
            mute_values.append(False)  # Muted tickers are excluded from the label

        # Generate the list of ticker input rows
        children = []
        for i, (ticker, invert, mute) in enumerate(zip(tickers, invert_values, mute_values)):
            row = create_primary_ticker_row(i, ticker, invert, mute)
            children.append(row)

        return children

    else:
        raise PreventUpdate

def create_primary_ticker_row(index, ticker_value='', invert_value=False, mute_value=False):
    return dbc.Row([
        dbc.Col(
            dbc.Input(
                id={'type': 'primary-ticker-input', 'index': index},
                placeholder='Enter ticker (e.g., CENN)',
                type='text',
                debounce=True,
                value=ticker_value  # Set the value to the ticker
            ),
            width=4
        ),
        dbc.Col(
            dbc.Switch(
                id={'type': 'invert-primary-switch', 'index': index},
                label='Invert Signals',
                value=invert_value  # Set the switch value
            ),
            width=2
        ),
        dbc.Col(
            dbc.Switch(
                id={'type': 'mute-primary-switch', 'index': index},
                label='Mute',
                value=mute_value  # Set the switch value
            ),
            width=2
        ),
        dbc.Col(
            dbc.Button(
                'Delete',
                id={'type': 'delete-primary-button', 'index': index},
                color='danger',
                size='sm'
            ),
            width=2
        )
    ], className='mb-2', id={'type': 'primary-ticker-row', 'index': index}, key=str(uuid.uuid4()))

def reindex_children(children):
    # Re-index the children and update their IDs and keys
    for i, child in enumerate(children):
        # Update row index
        child['props']['id']['index'] = i
        child['key'] = str(uuid.uuid4())
        
        # Update all components within the row
        for col in child['props']['children']:
            component = col['props']['children']
            if isinstance(component, dict):
                if 'props' in component:
                    # Update ID in props if it exists
                    if 'id' in component['props'] and isinstance(component['props']['id'], dict):
                        component['props']['id']['index'] = i
                # Update direct ID if it exists
                elif 'id' in component and isinstance(component['id'], dict):
                    component['id']['index'] = i
    
    logger.info(f"Reindexed {len(children)} rows with indices: {[child['props']['id']['index'] for child in children]}")
    return children

# Callback to process aggregated signals and update the chart and metrics table
@app.callback(
    [Output('multi-primary-chart', 'figure'),
     Output('multi-primary-metrics-table', 'data'),
     Output('multi-primary-metrics-table', 'columns'),
     Output('multi-secondary-feedback', 'children')],
    [Input({'type': 'primary-ticker-input', 'index': ALL}, 'value'),
     Input({'type': 'invert-primary-switch', 'index': ALL}, 'value'),
     Input({'type': 'mute-primary-switch', 'index': ALL}, 'value'),
     Input('multi-secondary-ticker-input', 'value'),
     Input('primary-tickers-container', 'children')],  # Added this input
    [State('update-interval', 'n_intervals')]
)
def update_multi_primary_outputs(primary_tickers, invert_signals, mute_signals, secondary_tickers_input, primary_tickers_children, n_intervals):
    if not secondary_tickers_input:
        return no_update, no_update, no_update, 'Please enter at least one secondary ticker.'

    # Filter out empty or muted primary tickers
    primary_tickers_filtered = []
    invert_signals_filtered = []
    for ticker, invert, mute in zip(primary_tickers, invert_signals, mute_signals):
        if ticker and not mute:
            primary_tickers_filtered.append(ticker.strip().upper())
            invert_signals_filtered.append(invert)

    if not primary_tickers_filtered:
        return no_update, no_update, no_update, 'Please enter at least one primary ticker.'

    # Parse secondary tickers
    secondary_tickers = [ticker.strip().upper() for ticker in secondary_tickers_input.split(',') if ticker.strip()]
    if not secondary_tickers:
        return no_update, no_update, no_update, 'Please enter at least one secondary ticker.'

    # Load primary tickers data
    primary_signals_list = []
    date_indexes = []
    for idx, (ticker, invert) in enumerate(zip(primary_tickers_filtered, invert_signals_filtered)):
        results = load_precomputed_results(ticker)
        if not results:
            return no_update, no_update, no_update, f'Processing Data for primary ticker {ticker}. Please wait.'
        signals = results.get('active_pairs')
        dates = results['preprocessed_data'].index

        # Create signals_series from signals and dates
        signals_series = pd.Series(signals, index=dates)

        # Process signals to extract 'Buy', 'Short', or 'None'
        signals_series = signals_series.astype(str)
        processed_signals = signals_series.apply(
            lambda x: 'Buy' if x.strip().startswith('Buy') else
                      'Short' if x.strip().startswith('Short') else 'None'
        )

        # Apply inversion if necessary
        if invert:
            processed_signals = processed_signals.replace({'Buy': 'Short', 'Short': 'Buy'})

        # Store the processed signals in a list
        primary_signals_list.append(processed_signals)
        date_indexes.append(set(processed_signals.index))

    # Find common dates among all primary tickers
    common_dates = set.intersection(*date_indexes)
    common_dates = sorted(common_dates)

    if not common_dates:
        return no_update, no_update, no_update, 'No overlapping dates among primary tickers.'

    # Combine signals into a DataFrame
    signals_df = pd.DataFrame({f'primary_{i}': sig.loc[common_dates] for i, sig in enumerate(primary_signals_list)})

    # Function to determine combined signal
    def get_combined_signal(row):
        # Validate input and handle None values
        if row is None or len(row) == 0:
            return 'None'
            
        # List of signals excluding 'None'
        active_signals = [s for s in row if s is not None and s != 'None']

        if not active_signals:
            return 'None'

        # Check if all active signals are the same
        if all(s == active_signals[0] for s in active_signals):
            return active_signals[0]
        else:
            return 'None'  # Signals are mixed and cancel out

    # Apply the combination function
    combined_signals = signals_df.apply(get_combined_signal, axis=1)

    # Initialize figure
    fig = go.Figure()
    metrics_data = []

    # Process each secondary ticker
    for secondary_ticker in secondary_tickers:
        # Fetch data for secondary ticker
        secondary_data = fetch_data(secondary_ticker)
        if secondary_data is None or secondary_data.empty:
            continue  # Skip this ticker if data is unavailable

        # Align dates with combined signals
        common_dates_sec = combined_signals.index.intersection(secondary_data.index)
        if len(common_dates_sec) < 2:
            continue  # Skip if insufficient data overlap

        signals = combined_signals.loc[common_dates_sec].astype(str)
        prices = secondary_data['Close'].loc[common_dates_sec]

        # Reindex signals and prices to a common index
        common_index = signals.index.union(prices.index)
        signals = signals.reindex(common_index).fillna('None')
        prices = prices.reindex(common_index).ffill()

        # Compute daily returns
        daily_returns = prices.pct_change().fillna(0)

        # Ensure signals and daily_returns have the same index
        signals = signals.loc[daily_returns.index]

        # Initialize daily_captures as float
        daily_captures = pd.Series(0.0, index=signals.index, dtype='float64')

        buy_mask = signals == 'Buy'
        short_mask = signals == 'Short'

        daily_captures[buy_mask] = daily_returns[buy_mask] * 100
        daily_captures[short_mask] = -daily_returns[short_mask] * 100

        cumulative_captures = daily_captures.cumsum()

        # Prepare metrics
        trigger_days = (buy_mask | short_mask).sum()
        wins = (daily_captures > 0).sum()
        losses = (daily_captures <= 0).sum()
        win_ratio = (wins / trigger_days * 100) if trigger_days > 0 else 0
        # Calculate metrics only on trigger days (buy or short)
        trigger_mask = buy_mask | short_mask
        avg_daily_capture = daily_captures[trigger_mask].mean() if trigger_days > 0 else 0
        total_capture = cumulative_captures.iloc[-1] if not cumulative_captures.empty else 0
        std_dev = daily_captures[trigger_mask].std() if trigger_days > 0 else 0
        # Ensure losses is calculated correctly
        losses = trigger_days - wins  # This ensures losses + wins = trigger_days

        risk_free_rate = 5.0  # 5% annual rate
        daily_rf_rate = risk_free_rate / 252  # Convert to daily rate
        sharpe_ratio = ((avg_daily_capture - daily_rf_rate) / std_dev) * np.sqrt(252) if std_dev > 0 else 0
        # Calculate statistical significance
        if trigger_days > 1 and std_dev > 0:
            t_statistic = (avg_daily_capture) / (std_dev / np.sqrt(trigger_days))
            degrees_of_freedom = trigger_days - 1
            p_value = 2 * (1 - stats.t.cdf(abs(t_statistic), df=degrees_of_freedom))
            t_statistic = round(t_statistic, 4)
            p_value = round(p_value, 4)
        else:
            t_statistic = None
            p_value = None

        metrics_data.append({
            'Secondary Ticker': secondary_ticker.upper(),
            'Trigger Days': int(trigger_days),
            'Wins': int(wins),
            'Losses': int(losses),
            'Win Ratio (%)': round(win_ratio, 2),
            'Std Dev (%)': round(std_dev, 4),
            'Sharpe Ratio': round(sharpe_ratio, 2),
            't-Statistic': t_statistic if t_statistic is not None else 'N/A',
            'p-Value': p_value if p_value is not None else 'N/A',
            'Significant 90%': 'Yes' if p_value is not None and p_value < 0.10 else 'No',
            'Significant 95%': 'Yes' if p_value is not None and p_value < 0.05 else 'No',
            'Significant 99%': 'Yes' if p_value is not None and p_value < 0.01 else 'No',
            'Avg Daily Capture (%)': round(avg_daily_capture, 4),
            'Total Capture (%)': round(total_capture, 4)
        })

        # Add trace to figure
        fig.add_trace(go.Scatter(
            x=cumulative_captures.index,
            y=cumulative_captures.values,
            mode='lines',
            name=secondary_ticker.upper(),
            line=dict(width=2),
        ))

    if not metrics_data:
        return no_update, no_update, no_update, 'No valid data for secondary tickers.'

    columns = [{'name': col, 'id': col} for col in metrics_data[0].keys()]

    # Update figure layout
    fig.update_layout(
        title=dict(
            text='Combined Signals Capture for Secondary Tickers',
            font=dict(color='#80ff00')
        ),
        xaxis_title='Date',
        yaxis_title='Cumulative Capture (%)',
        template='plotly_dark',
        font=dict(color='#80ff00'),
        plot_bgcolor='black',
        paper_bgcolor='black',
        xaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        ),
        yaxis=dict(
            color='#80ff00',
            showgrid=True,
            gridcolor='#80ff00',
            zerolinecolor='#80ff00',
            linecolor='#80ff00',
            tickfont=dict(color='#80ff00')
        )
    )

    return fig, metrics_data, columns, ''

# Global variables for processing queue, worker thread, and all tickers
ticker_queue = []
all_tickers = set()
processing_thread = None
processing_lock = threading.Lock()

# -----------------------------------------------------------------------------
# Batch Processing and Optimization Callbacks
# -----------------------------------------------------------------------------
@app.callback(
    [Output('batch-process-table', 'data'),
     Output('batch-ticker-input-feedback', 'children')],
    [Input('batch-process-button', 'n_clicks'),
     Input('batch-update-interval', 'n_intervals')],
    [State('batch-ticker-input', 'value'),
     State('batch-process-table', 'data')],
    prevent_initial_call=True
)
def batch_process_tickers(n_clicks, n_intervals, tickers_input, existing_table_data):
    ctx = dash.callback_context
    triggered_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if triggered_id == 'batch-process-button':
        if not tickers_input:
            return existing_table_data or [], 'Please enter at least one ticker symbol.'
    
        tickers = [ticker.strip().upper() for ticker in tickers_input.split(',') if ticker.strip()]
        if not tickers:
            return existing_table_data or [], 'Please enter valid ticker symbols.'
    
        # Add tickers to the processing queue and all_tickers set
        with processing_lock:
            for ticker in tickers:
                all_tickers.add(ticker)
                if ticker not in ticker_queue:
                    ticker_queue.append(ticker)
    
        # Start the processing thread if not already running
        global processing_thread
        if processing_thread is None or not processing_thread.is_alive():
            processing_thread = threading.Thread(target=process_ticker_queue, daemon=True)
            processing_thread.start()
    
        return existing_table_data or [], ''
    else:
        # Interval triggered: Update the DataTable
        table_data = []
        tickers_to_check = list(all_tickers)
        for ticker in tickers_to_check:
            status = read_status(ticker)
            if status['status'] == 'complete':
                results = load_precomputed_results(ticker)
                if results is None:
                    continue
                    
                # Validate required data exists
                required_keys = ['preprocessed_data', 'daily_top_buy_pairs', 'daily_top_short_pairs', 
                               'top_buy_pair', 'top_short_pair']
                if not all(key in results for key in required_keys):
                    logger.error(f"Missing required keys in results for {ticker}")
                    last_date = 'Missing Data'
                    last_price = 'N/A'
                    next_day_signal = 'Invalid Data'
                    processing_status = 'Error'
                else:
                    df = results['preprocessed_data']
                    if df is None or df.empty:
                        last_date = 'No Data'
                        last_price = 'N/A'
                        next_day_signal = 'No Data'
                        processing_status = 'Error'
                        continue
                    
                    # Get the most recent valid trading day
                    last_valid_date = None
                    # Make sure we're working with tz-naive dates throughout
                    df.index = df.index.tz_localize(None)
                    
                    for date in sorted(df.index, reverse=True):
                        if pd.notna(df.loc[date, 'Close']):
                            last_valid_date = date
                            break
                    
                    if last_valid_date is None:
                        last_date = 'No Valid Date'
                        last_price = 'N/A'
                        next_day_signal = 'No Valid Date'
                        processing_status = 'Error'
                    else:
                        # Display date only
                        last_date = last_valid_date.strftime('%Y-%m-%d')
                        
                        # Get the last price from the valid trading day
                        # Convert back to tz-naive for lookup
                        if 'Adj Close' in df.columns:
                            last_price = df.loc[last_valid_date.tz_localize(None), 'Adj Close']
                        else:
                            last_price = df.loc[last_valid_date.tz_localize(None), 'Close']
                        last_price = f"${last_price:.2f}"
                        
                        # Get next day signal with validation
                        buy_pair = results.get('top_buy_pair')
                        short_pair = results.get('top_short_pair')
                        
                        if not all(isinstance(pair, tuple) and len(pair) == 2 for pair in [buy_pair, short_pair]):
                            next_day_signal = 'Invalid pairs'
                        else:
                            try:
                                # Validate SMA columns exist
                                required_smas = [
                                    f'SMA_{buy_pair[0]}', f'SMA_{buy_pair[1]}',
                                    f'SMA_{short_pair[0]}', f'SMA_{short_pair[1]}'
                                ]
                                
                                if not all(sma in df.columns for sma in required_smas):
                                    next_day_signal = 'Missing SMAs'
                                else:
                                    # Get SMAs for the last valid date (using tz-naive index)
                                    lookup_date = last_valid_date.tz_localize(None)
                                    sma1_buy = df.loc[lookup_date, f'SMA_{buy_pair[0]}']
                                    sma2_buy = df.loc[lookup_date, f'SMA_{buy_pair[1]}']
                                    sma1_short = df.loc[lookup_date, f'SMA_{short_pair[0]}']
                                    sma2_short = df.loc[lookup_date, f'SMA_{short_pair[1]}']
                                    
                                    # Check for NaN values
                                    if any(pd.isna([sma1_buy, sma2_buy, sma1_short, sma2_short])):
                                        next_day_signal = 'NaN in SMAs'
                                    else:
                                        # Calculate signals
                                        buy_signal = sma1_buy > sma2_buy
                                        short_signal = sma1_short < sma2_short
                                        
                                        if buy_signal and short_signal:
                                            buy_capture = results.get('top_buy_capture', 0)
                                            short_capture = results.get('top_short_capture', 0)
                                            next_day_signal = f"Buy ({buy_pair[0]},{buy_pair[1]})" if buy_capture > short_capture else f"Short ({short_pair[0]},{short_pair[1]})"
                                        elif buy_signal:
                                            next_day_signal = f"Buy ({buy_pair[0]},{buy_pair[1]})"
                                        elif short_signal:
                                            next_day_signal = f"Short ({short_pair[0]},{short_pair[1]})"
                                        else:
                                            next_day_signal = 'None'
                            except Exception as e:
                                logger.error(f"Error calculating signal for {ticker}: {str(e)}")
                                next_day_signal = 'Error'
                                
                        processing_status = 'Complete'
            elif status['status'] == 'failed':
                last_date = 'N/A'
                last_price = 'N/A'
                next_day_signal = 'N/A'
                processing_status = 'Failed'
            elif status['status'] == 'processing':
                last_date = 'N/A'
                last_price = 'N/A'
                next_day_signal = 'N/A'
                processing_status = 'Processing'
            else:
                last_date = 'N/A'
                last_price = 'N/A'
                next_day_signal = 'N/A'
                processing_status = 'Pending'
    
            table_data.append({
                'Ticker': ticker,
                'Last Date': last_date,
                'Last Price': last_price,
                'Next Day Active Signal': next_day_signal,
                'Processing Status': processing_status
            })
    
        # Sort the table_data list alphabetically by 'Ticker'
        table_data.sort(key=lambda x: x['Ticker'])
        return table_data, ''

def process_ticker_queue():
    while True:
        with processing_lock:
            if not ticker_queue:
                break
            ticker = ticker_queue.pop(0)
        # Update status to processing
        write_status(ticker, {'status': 'processing', 'progress': 0})
        event = threading.Event()
        precompute_results(ticker, event)
        # After processing, update status
        write_status(ticker, {'status': 'complete', 'progress': 100})

@app.callback(
    [Output('optimization-results-table', 'data'),
     Output('optimization-results-table', 'columns'),
     Output('optimization-feedback', 'children'),
     Output('optimization-update-interval', 'disabled')],
    [Input('optimize-signals-button', 'n_clicks'),
     Input('optimization-update-interval', 'n_intervals'),
     Input('optimization-results-table', 'sort_by')],
    [State('optimization-primary-tickers', 'value'),
     State('optimization-secondary-ticker', 'value')],
    prevent_initial_call=True
)
def optimize_signals(n_clicks, n_intervals, sort_by, primary_tickers_input, secondary_ticker_input):
    global optimization_in_progress
    empty_columns = [{'name': i, 'id': i} for i in ['Combination']]
    
    try:
        ctx = dash.callback_context
        triggered_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        # Validate inputs
        if not primary_tickers_input or not secondary_ticker_input:
            raise PreventUpdate
        
        primary_tickers_input = primary_tickers_input.strip()
        secondary_ticker_input = secondary_ticker_input.strip()
        
        if not primary_tickers_input or not secondary_ticker_input:
            raise PreventUpdate

        # Check cache first for any request type
        if primary_tickers_input and secondary_ticker_input:
            cache_key = f"{primary_tickers_input}_{secondary_ticker_input}"
            if cache_key in optimization_results_cache:
                cached_results, cached_columns, cached_message, cached_sort = optimization_results_cache[cache_key]
                
                # If this is a sort request, update the cached sort state
                if triggered_id == 'optimization-results-table':
                    current_sort = sort_by
                else:
                    current_sort = cached_sort
                
                if current_sort:
                    # Apply cached sort (rest of sorting logic remains the same)
                    averages_row = next((row for row in cached_results if row['Combination'] == 'AVERAGES'), None)
                    sortable_data = [row for row in cached_results if row['Combination'] != 'AVERAGES']
                    
                    for sort_spec in current_sort:
                        col_id = sort_spec['column_id']
                        is_ascending = sort_spec['direction'] == 'asc'
                        try:
                            if col_id in ['Trigger Days', 'Wins', 'Losses', 'Win Ratio (%)', 
                                        'Std Dev (%)', 'Sharpe Ratio', 'Avg Daily Capture (%)', 
                                        'Total Capture (%)']:
                                sortable_data = sorted(
                                    sortable_data,
                                    key=lambda x: (float(str(x[col_id]).replace('N/A', '-inf'))
                                                 if x[col_id] != 'N/A' else float('-inf')),
                                    reverse=not is_ascending
                                )
                            else:
                                sortable_data = sorted(
                                    sortable_data,
                                    key=lambda x: str(x[col_id]),
                                    reverse=not is_ascending
                                )
                        except Exception as e:
                            logger.error(f"Sorting error for column {col_id}: {str(e)}")
                            continue
                    
                    sorted_results = [averages_row] + sortable_data if averages_row else sortable_data
                    # Update cache with new sort state
                    optimization_results_cache[cache_key] = (sorted_results, cached_columns, cached_message, current_sort)
                    return sorted_results, cached_columns, cached_message, True  # Add the fourth output

                return cached_results, cached_columns, cached_message, True  # Add the fourth output

        # Handle interval updates
        if triggered_id == 'optimization-update-interval':
            # Prevent processing if inputs are None or empty
            if not primary_tickers_input or not secondary_ticker_input:
                raise PreventUpdate
            cache_key = f"{primary_tickers_input}_{secondary_ticker_input}"
            if cache_key in optimization_results_cache:
                cached_results, cached_columns, cached_message, cached_sort = optimization_results_cache[cache_key]
                
                # If this is a sort request, handle it without reprocessing
                if ctx.triggered_id == 'optimization-results-table.sort_by' and cached_results:
                    # Separate averages row from sortable data
                    averages_row = next((row for row in cached_results if row['Combination'] == 'AVERAGES'), None)
                    sortable_data = [row for row in cached_results if row['Combination'] != 'AVERAGES']
                    
                    # Apply sorting if requested
                    if sort_by:
                        for sort_spec in sort_by:
                            col_id = sort_spec['column_id']
                            is_ascending = sort_spec['direction'] == 'asc'
                            # Handle different column types
                            if col_id in ['Trigger Days', 'Wins', 'Losses', 'Win Ratio (%)', 
                                        'Std Dev (%)', 'Sharpe Ratio', 'Avg Daily Capture (%)', 
                                        'Total Capture (%)']:
                                sortable_data = sorted(sortable_data,
                                                     key=lambda x: float(x[col_id]) if x[col_id] != 'N/A' else float('-inf'),
                                                     reverse=not is_ascending)
                            else:
                                sortable_data = sorted(sortable_data,
                                                     key=lambda x: str(x[col_id]),
                                                     reverse=not is_ascending)
                    
                    # Return sorted data with averages row at top
                    if averages_row:
                        sorted_results = [averages_row] + sortable_data
                    else:
                        sorted_results = sortable_data
                        
                    return sorted_results, cached_columns, cached_message, False  # Keep the interval active

                # For non-sort requests, verify processing status
                primary_tickers = [ticker.strip().upper() for ticker in primary_tickers_input.split(',') if ticker.strip()]
                all_processed = all(
                    read_status(ticker).get('status') == 'complete'
                    for ticker in primary_tickers
                )
                if all_processed:
                    return optimization_results_cache[cache_key][:3] + (True,)
                
            # Check processing status of primary tickers
            primary_tickers = [ticker.strip().upper() for ticker in primary_tickers_input.split(',') if ticker.strip()]
            processing_statuses = []
            completed_tickers = []
            any_processing = False
            needs_processing = False
            
            for ticker in primary_tickers:
                status = read_status(ticker)
                if status['status'] == 'processing':
                    any_processing = True
                    processing_statuses.append(f"{ticker}: {status['progress']:.1f}%")
                elif status['status'] == 'complete':
                    completed_tickers.append(ticker)
                elif status['status'] in ['not started', 'failed']:
                    needs_processing = True
                    processing_statuses.append(f"{ticker}: Waiting...")
                elif status['status'] == 'failed':
                    processing_statuses.append(f"{ticker}: Failed")
                    
            if any_processing or needs_processing:
                status_message = f"Processing: {', '.join(processing_statuses)}"
                if completed_tickers:
                    status_message += f" | Completed: {', '.join(completed_tickers)}"
                return [], empty_columns, status_message, False  # Keep the interval active

            # After handling everything, prevent further updates
            raise PreventUpdate

        # Handle button click to start optimization
        if triggered_id == 'optimize-signals-button':
            if n_clicks is None or n_clicks == 0:
                raise PreventUpdate  # Button has not been clicked
            
            if optimization_in_progress:
                return [], empty_columns, "Optimization already in progress. Please wait...", False  # Keep interval disabled

            # Acquire lock for new processing
            if not optimization_lock.acquire(blocking=False):
                return [], empty_columns, "Another optimization is in progress. Please wait...", False  # Keep interval disabled

            optimization_in_progress = True

            # Proceed to processing code without returning immediately
            # Remove the 'return' statement here to allow the processing to proceed

        # Basic input validation
        if not primary_tickers_input or not secondary_ticker_input:
            if optimization_in_progress:
                optimization_in_progress = False
                if optimization_lock.locked():
                    optimization_lock.release()
            return [], empty_columns, 'Please enter both primary and secondary tickers.'

        # Parse tickers
        primary_tickers = [ticker.strip().upper() for ticker in primary_tickers_input.split(',') if ticker.strip()]
        secondary_tickers = [ticker.strip().upper() for ticker in secondary_ticker_input.split(',') if ticker.strip()]
        if len(secondary_tickers) != 1:
            return [], empty_columns, 'Please enter exactly one secondary ticker.'
        secondary_ticker = secondary_tickers[0]

        # Limit the number of primary tickers
        max_primary_tickers = 18 # Limit to 18 tickers for performance
        if len(primary_tickers) > max_primary_tickers:
            return [], empty_columns, f'Please enter {max_primary_tickers} or fewer primary tickers to limit computation time.'

        # Fetch secondary ticker data
        secondary_data = fetch_data(secondary_ticker)
        if secondary_data is None or secondary_data.empty:
            return [], empty_columns, f'No data found for secondary ticker {secondary_ticker}.'

        # Fetch data for each primary ticker
        primary_signals = {}
        date_indexes = {}
        for ticker in primary_tickers:
            results = load_precomputed_results(ticker)
            if not results or 'active_pairs' not in results:
                return [], empty_columns, f'Data not processed for primary ticker {ticker}. Please wait.'

            active_pairs = results['active_pairs']
            dates = results['preprocessed_data'].index

            # Handle length mismatch
            if len(active_pairs) != len(dates):
                if len(active_pairs) == len(dates) - 1:
                    dates = dates[1:]
                else:
                    return [], empty_columns, f'Length mismatch between active_pairs and dates for ticker {ticker}. Cannot proceed.'

            # Create signals series
            signals_series = pd.Series(active_pairs, index=dates)
            
            # Process for next day's signals
            if 'preprocessed_data' in results and 'daily_top_buy_pairs' in results and 'daily_top_short_pairs' in results:
                df = results['preprocessed_data']
                last_date = df.index[-1]
                buy_pair_data = results['daily_top_buy_pairs'].get(last_date)
                short_pair_data = results['daily_top_short_pairs'].get(last_date)
                
                if buy_pair_data and short_pair_data:
                    try:
                        # Validate pair data structure
                        if not isinstance(buy_pair_data[0], tuple) or not isinstance(short_pair_data[0], tuple):
                            raise ValueError("Invalid pair data structure")
                                
                        # Calculate next day's signal
                        buy_pair = buy_pair_data[0]
                        short_pair = short_pair_data[0]
                        buy_capture = buy_pair_data[1]
                        short_capture = short_pair_data[1]
                            
                        # Validate SMA columns exist
                        required_smas = [
                            f'SMA_{buy_pair[0]}', f'SMA_{buy_pair[1]}',
                            f'SMA_{short_pair[0]}', f'SMA_{short_pair[1]}'
                        ]
                        if not all(sma in df.columns for sma in required_smas):
                            raise ValueError("Missing required SMA columns")
                            
                        buy_signal = df[f'SMA_{buy_pair[0]}'].loc[last_date] > df[f'SMA_{buy_pair[1]}'].loc[last_date]
                        short_signal = df[f'SMA_{short_pair[0]}'].loc[last_date] < df[f'SMA_{short_pair[1]}'].loc[last_date]
                            
                        # Determine next signal
                        if buy_signal and short_signal:
                            next_signal = f"Buy" if buy_capture > short_capture else f"Short"
                        elif buy_signal:
                            next_signal = f"Buy"
                        elif short_signal:
                            next_signal = f"Short"
                        else:
                            next_signal = "None"
                            
                        # Store current signals for performance calculation
                        processed_signals = signals_series.astype(str).apply(
                            lambda x: 'Buy' if x.strip().startswith('Buy') else
                                    'Short' if x.strip().startswith('Short') else 'None'
                        )
                        
                        # Append next_signal to processed_signals
                        next_date = secondary_data.index[secondary_data.index > last_date]
                        if not next_date.empty:
                            next_date = next_date[0]
                            processed_signals = pd.concat([processed_signals, pd.Series([next_signal], index=[next_date])])
                        else:
                            # No future date available, cannot append next_signal
                            pass
             
                        # Only log signals during initial processing, not during sorts or interval updates
                        if ctx.triggered_id not in ['optimization-results-table.sort_by', 'optimization-update-interval']:
                            logger.info(f"Ticker {ticker} - Next signal: {next_signal}")
                            
                        primary_signals[ticker] = {
                            'signals_with_next': processed_signals,
                            'next_signal': next_signal
                        }
                        date_indexes[ticker] = set(processed_signals.index)
                            
                    except Exception as e:
                        logger.error(f"Error processing signals for {ticker}: {str(e)}")
                        return [], empty_columns, f'Error processing signals for {ticker}.'
                else:
                    return [], empty_columns, f'Incomplete data for ticker {ticker}.'
            else:
                return [], empty_columns, f'Missing data in results for ticker {ticker}.'

        # Generate possible states for each ticker based on next day's signals
        ticker_states = {}
        for ticker in primary_tickers:
            signal = primary_signals[ticker]['next_signal']
            logger.debug(f"Using next day signal for {ticker}: {signal}")
            
            # Determine possible states based on next signal
            if 'Buy' in signal:
                ticker_states[ticker] = [(False, False), (False, True)]  # (invert_signals, mute)
            elif 'Short' in signal:
                ticker_states[ticker] = [(True, False), (False, True)]  # (invert_signals, mute)
            else:
                ticker_states[ticker] = [(False, True)]  # Only mute option for 'None' signals

        # Generate combinations as an iterator
        ticker_state_lists = list(ticker_states.values())
        combinations = product(*ticker_state_lists)  # Do not convert to list to save memory
        combination_labels = []
        valid_combinations = []

        for states in combinations:
            label_parts = []
            state_dict = {}
            
            for ticker, (invert_signals, mute) in zip(ticker_states.keys(), states):
                if mute:
                    state_dict[ticker] = {'invert_signals': invert_signals, 'mute': mute}
                    continue  # Skip muted tickers in label
                
                # Get next day's signal for display
                next_signal = primary_signals[ticker]['next_signal']
                if invert_signals:
                    # Invert the signal for display
                    if 'Buy' in next_signal:
                        display_signal = 'Short'
                    elif 'Short' in next_signal:
                        display_signal = 'Buy'
                    else:
                        display_signal = next_signal
                    label_parts.append(f"<span style='color:red'>{ticker}</span>")
                else:
                    display_signal = next_signal
                    label_parts.append(f"<span style='color:#80ff00'>{ticker}</span>")
                
                state_dict[ticker] = {'invert_signals': invert_signals, 'mute': mute}
            
            label = ', '.join(label_parts)
            combination_labels.append(label)
            valid_combinations.append(state_dict)

        # Calculate total number of combinations
        from functools import reduce
        import operator

        total_combinations = reduce(operator.mul, [len(states) for states in ticker_state_lists], 1)
        logger.info(f"Total combinations to process: {total_combinations}")

        # Prepare for results
        results_list = []

        # Process each combination with a single progress bar
        from tqdm import tqdm

        logger.info(f"Total combinations to process: {len(valid_combinations)}")
        with tqdm(total=len(valid_combinations), desc="Calculating metrics for combinations") as pbar:
            for idx, state_dict in enumerate(valid_combinations):
                
                # Get unmuted tickers
                unmuted_tickers = [ticker for ticker in primary_tickers 
                                if ticker in state_dict and not state_dict[ticker]['mute']]

                if not unmuted_tickers:
                    pbar.update(1)
                    continue  # Skip if all tickers are muted

                # Find common dates
                common_dates = set(secondary_data.index)
                for ticker in unmuted_tickers:
                    common_dates = common_dates.intersection(date_indexes[ticker])
                common_dates = sorted(common_dates)

                if not common_dates:
                    pbar.update(1)
                    continue  # Skip if no overlapping dates

                # Build combined signals DataFrame for performance calculation
                combined_signals_df = pd.DataFrame(index=common_dates)
                for ticker in unmuted_tickers:
                    state = state_dict[ticker]
                    invert_signals = state['invert_signals']
                    
                    # Use signals_with_next for performance calculation
                    signals_with_next = primary_signals[ticker]['signals_with_next'].loc[common_dates]
                    
                    # Apply inversion if needed
                    if invert_signals:
                        signals = signals_with_next.replace({'Buy': 'Short', 'Short': 'Buy'})
                    else:
                        signals = signals_with_next
                    
                    combined_signals_df[ticker] = signals

                # Combine signals using vectorization without deprecated 'applymap' method
                signal_mapping = {'Buy': 1, 'Short': -1, 'None': 0}

                # Apply mapping using 'apply' and 'map' to avoid FutureWarning
                signal_values = combined_signals_df.apply(lambda col: col.map(signal_mapping)).values.astype(int)

                sum_signals = np.sum(signal_values, axis=1)
                signal_counts = np.count_nonzero(signal_values != 0, axis=1)

                # Determine combined signals
                combined_signals_array = np.where(
                    signal_counts == 0, 'None',
                    np.where(
                        sum_signals == signal_counts, 'Buy',
                        np.where(
                            sum_signals == -signal_counts, 'Short',
                            'None'
                        )
                    )
                )
                combined_signals = pd.Series(combined_signals_array, index=combined_signals_df.index)

                # No need to shift signals since we included the next day's signal
                signals = combined_signals.fillna('None')

                # Align signals and prices
                prices = secondary_data['Close'].loc[signals.index]
                daily_returns = prices.pct_change().fillna(0)

                # Ensure signals and daily_returns have the same index
                signals = signals.loc[daily_returns.index]

                # Calculate daily captures
                daily_captures = pd.Series(0.0, index=signals.index)
                buy_mask = signals == 'Buy'
                short_mask = signals == 'Short'
                
                daily_captures[buy_mask] = daily_returns[buy_mask] * 100
                daily_captures[short_mask] = -daily_returns[short_mask] * 100

                # Calculate metrics
                trigger_days = (buy_mask | short_mask).sum()
                if trigger_days == 0:
                    pbar.update(1)
                    continue  # Skip combinations with no triggers

                # Calculate wins and losses
                trigger_captures = daily_captures[buy_mask | short_mask]
                wins = (trigger_captures > 0).sum()
                losses = trigger_days - wins
                win_ratio = (wins / trigger_days * 100) if trigger_days > 0 else 0

                # Calculate performance metrics
                avg_daily_capture = trigger_captures.mean() if trigger_days > 0 else 0
                total_capture = trigger_captures.sum() if trigger_days > 0 else 0
                std_dev = trigger_captures.std() if trigger_days > 0 else 0

                # Calculate Sharpe ratio
                risk_free_rate = 5.0  # 5% annual rate
                daily_rf_rate = risk_free_rate / 252
                annualized_return = avg_daily_capture * 252
                annualized_std = std_dev * np.sqrt(252) if std_dev > 0 else 0
                sharpe_ratio = ((annualized_return - risk_free_rate) / annualized_std) if annualized_std > 0 else 0

                # Calculate statistical significance
                if trigger_days > 1 and std_dev > 0:
                    t_statistic = (avg_daily_capture) / (std_dev / np.sqrt(trigger_days))
                    degrees_of_freedom = trigger_days - 1
                    p_value = 2 * (1 - stats.t.cdf(abs(t_statistic), df=degrees_of_freedom))
                    t_statistic = round(t_statistic, 4)
                    p_value = round(p_value, 4)
                else:
                    t_statistic = None
                    p_value = None

                # Store results
                results_list.append({
                    'id': idx,  # Add a unique identifier
                    'Combination': combination_labels[idx],
                    'Trigger Days': int(trigger_days),
                    'Wins': int(wins),
                    'Losses': int(losses),
                    'Win Ratio (%)': round(win_ratio, 2),
                    'Std Dev (%)': round(std_dev, 4),
                    'Sharpe Ratio': round(sharpe_ratio, 2),
                    't-Statistic': t_statistic if t_statistic is not None else 'N/A',
                    'p-Value': p_value if p_value is not None else 'N/A',
                    'Significant 90%': 'Yes' if p_value is not None and p_value < 0.10 else 'No',
                    'Significant 95%': 'Yes' if p_value is not None and p_value < 0.05 else 'No',
                    'Significant 99%': 'Yes' if p_value is not None and p_value < 0.01 else 'No',
                    'Avg Daily Capture (%)': round(avg_daily_capture, 4),
                    'Total Capture (%)': round(total_capture, 4)
                })

                # Update progress bar
                pbar.update(1)

        if not results_list:
            if optimization_in_progress:
                optimization_in_progress = False
                if optimization_lock.locked():
                    optimization_lock.release()
            return [], empty_columns, 'No valid combinations found.', True  # Add the fourth output

        # Sort by Sharpe Ratio
        results_list.sort(key=lambda x: x['Sharpe Ratio'], reverse=True)

        # Define columns for the DataTable
        columns = [
            {'name': 'Combination', 'id': 'Combination', 'presentation': 'markdown'},
            {'name': 'Trigger Days', 'id': 'Trigger Days', 'type': 'numeric'},
            {'name': 'Wins', 'id': 'Wins', 'type': 'numeric'},
            {'name': 'Losses', 'id': 'Losses', 'type': 'numeric'},
            {'name': 'Win Ratio (%)', 'id': 'Win Ratio (%)', 'type': 'numeric'},
            {'name': 'Std Dev (%)', 'id': 'Std Dev (%)', 'type': 'numeric'},
            {'name': 'Sharpe Ratio', 'id': 'Sharpe Ratio', 'type': 'numeric'},
            {'name': 't-Statistic', 'id': 't-Statistic'},
            {'name': 'p-Value', 'id': 'p-Value'},
            {'name': 'Significant 90%', 'id': 'Significant 90%'},
            {'name': 'Significant 95%', 'id': 'Significant 95%'},
            {'name': 'Significant 99%', 'id': 'Significant 99%'},
            {'name': 'Avg Daily Capture (%)', 'id': 'Avg Daily Capture (%)', 'type': 'numeric'},
            {'name': 'Total Capture (%)', 'id': 'Total Capture (%)', 'type': 'numeric'}
        ]

        try:
            # Calculate averages for numeric columns
            if results_list:
                averages = {
                    'Combination': 'AVERAGES',
                    'Trigger Days': round(sum(r['Trigger Days'] for r in results_list) / len(results_list)),
                    'Wins': round(sum(r['Wins'] for r in results_list) / len(results_list)),
                    'Losses': round(sum(r['Losses'] for r in results_list) / len(results_list)),
                    'Win Ratio (%)': round(sum(r['Win Ratio (%)'] for r in results_list) / len(results_list), 2),
                    'Std Dev (%)': round(sum(r['Std Dev (%)'] for r in results_list) / len(results_list), 4),
                    'Sharpe Ratio': round(sum(r['Sharpe Ratio'] for r in results_list) / len(results_list), 2),
                    't-Statistic': round(sum(float(r['t-Statistic']) if r['t-Statistic'] != 'N/A' else 0 for r in results_list) / 
                                      sum(1 for r in results_list if r['t-Statistic'] != 'N/A'), 4) if any(r['t-Statistic'] != 'N/A' for r in results_list) else 'N/A',
                    'p-Value': round(sum(float(r['p-Value']) if r['p-Value'] != 'N/A' else 0 for r in results_list) / 
                                   sum(1 for r in results_list if r['p-Value'] != 'N/A'), 4) if any(r['p-Value'] != 'N/A' for r in results_list) else 'N/A',
                    'Significant 90%': f"{round(sum(1 for r in results_list if r['Significant 90%'] == 'Yes') / len(results_list) * 100, 1)}% of combinations",
                    'Significant 95%': f"{round(sum(1 for r in results_list if r['Significant 95%'] == 'Yes') / len(results_list) * 100, 1)}% of combinations",
                    'Significant 99%': f"{round(sum(1 for r in results_list if r['Significant 99%'] == 'Yes') / len(results_list) * 100, 1)}% of combinations",
                    'Avg Daily Capture (%)': round(sum(r['Avg Daily Capture (%)'] for r in results_list) / len(results_list), 4),
                    'Total Capture (%)': round(sum(r['Total Capture (%)'] for r in results_list) / len(results_list), 4)
                }        
            # Handle sorting and fixed averages row
            cache_key = f"{primary_tickers_input}_{secondary_ticker_input}"
            if results_list:
                # Store current sort state with the cache
                current_sort = getattr(ctx.inputs, 'optimization-results-table.sort_by', None)
                sortable_data = sorted(results_list, key=lambda x: x['Sharpe Ratio'], reverse=True)
                
                # Apply current sort if exists
                if current_sort:
                    for sort_spec in current_sort:
                        col_id = sort_spec['column_id']
                        is_ascending = sort_spec['direction'] == 'asc'
                        try:
                            if col_id in ['Trigger Days', 'Wins', 'Losses', 'Win Ratio (%)', 
                                        'Std Dev (%)', 'Sharpe Ratio', 'Avg Daily Capture (%)', 
                                        'Total Capture (%)']:
                                sortable_data = sorted(
                                    sortable_data,
                                    key=lambda x: (float(str(x[col_id]).replace('N/A', '-inf'))
                                                 if x[col_id] != 'N/A' else float('-inf')),
                                    reverse=not is_ascending
                                )
                            else:
                                sortable_data = sorted(
                                    sortable_data,
                                    key=lambda x: str(x[col_id]),
                                    reverse=not is_ascending
                                )
                        except Exception as e:
                            logger.error(f"Sorting error for column {col_id}: {str(e)}")
                            continue
                
                fixed_results = [averages] + sortable_data
                optimization_results_cache[cache_key] = (fixed_results, columns, 'Optimization complete. Please verify the results by manually entering the target combination into the Multi-Primary Signal Aggregator.', current_sort)
            else:
                optimization_results_cache[cache_key] = ([], columns, 'No valid combinations found.', None)
            return optimization_results_cache[cache_key][:3] + (True,)

        finally:
            optimization_in_progress = False
            if optimization_lock.locked():
                optimization_lock.release()
                
    except PreventUpdate:
        raise  # Re-raise PreventUpdate without logging
    except Exception as e:
        logger.error(f"Error in optimize_signals: {str(e)}")
        logger.error(traceback.format_exc())
        if optimization_in_progress:
            optimization_in_progress = False
            if optimization_lock.locked():
                optimization_lock.release()
        return [], empty_columns, f"Error: {str(e)}", True  # Add the fourth output

# Add this variable at the top of your script with other globals
last_active_cell = None

@app.callback(
    [Output({'type': 'primary-ticker-input', 'index': ALL}, 'value'),
     Output({'type': 'invert-primary-switch', 'index': ALL}, 'value'),
     Output({'type': 'mute-primary-switch', 'index': ALL}, 'value')],
    [Input('optimization-results-table', 'active_cell')],
    [State('optimization-results-table', 'data'),
     State('optimization-results-table', 'page_current'),
     State('optimization-results-table', 'page_size'),
     State({'type': 'primary-ticker-input', 'index': ALL}, 'id')],
    prevent_initial_call=True
)
def populate_multi_primary_aggregator(active_cell, data, page_current, page_size, primary_input_ids):
    global last_active_cell

    if not active_cell:
        raise PreventUpdate

    # Check if this is the same cell click we already processed
    if last_active_cell == active_cell:
        raise PreventUpdate

    last_active_cell = active_cell

    if active_cell['column_id'] != 'Combination':
        raise PreventUpdate

    try:
        row = active_cell['row']

        # Calculate the absolute row index
        if page_current is not None and page_size is not None:
            absolute_row_index = row + page_current * page_size
        else:
            absolute_row_index = row  # Assume absolute index when pagination is disabled


        # Ensure the index is within the bounds of the data
        if absolute_row_index >= len(data):
            raise PreventUpdate

        row_data = data[absolute_row_index]
        combination_html = row_data['Combination']

        # Parse the HTML content (only log once)
        logger.info(f"Processing combination from absolute row index {absolute_row_index}")

        # Existing parsing logic
        soup = BeautifulSoup(combination_html, 'html.parser')
        parsed_data = []

        for span in soup.find_all('span'):
            ticker = span.text.strip()
            style = span.get('style', '')
            invert = 'color:red' in style or 'color: red' in style
            parsed_data.append({
                'ticker': ticker,
                'invert': invert,
                'mute': False
            })

        # Prepare outputs
        num_slots = len(primary_input_ids)
        ticker_values = []
        invert_values = []
        mute_values = []

        # Fill with parsed data
        for i in range(min(num_slots, len(parsed_data))):
            ticker_values.append(parsed_data[i]['ticker'])
            invert_values.append(parsed_data[i]['invert'])
            mute_values.append(parsed_data[i]['mute'])

        # Fill remaining slots with empty values
        while len(ticker_values) < num_slots:
            ticker_values.append('')
            invert_values.append(False)
            mute_values.append(False)

        logger.info(f"Configured {len(parsed_data)} tickers")
        return ticker_values, invert_values, mute_values

    except Exception as e:
        logger.error(f"Error processing combination: {str(e)}")
        logger.error(traceback.format_exc())
        raise PreventUpdate

@app.callback(
    Output("help-modal", "is_open"),
    [Input("help-button", "n_clicks"), Input("close-help", "n_clicks")],
    [State("help-modal", "is_open")],
    prevent_initial_call=True
)
def toggle_help_modal(n1, n2, is_open):
    # Toggle the Help modal open or closed when either the Help or Close button is clicked
    if n1 or n2:
        return not is_open
    return is_open

# Removed redundant test callback - ticker submission is already logged in validate_ticker_input

# ============================================================================
# MAIN EXECUTION
# ============================================================================
if __name__ == "__main__":
    import signal
    import atexit
    
    # Handler for graceful shutdown
    def signal_handler(sig, frame):
        logger.info(f"\n{Colors.YELLOW}[🛑] Shutting down server...{Colors.ENDC}")
        logger.info(f"{Colors.CYAN}[👋] Thank you for using PRJCT9!{Colors.ENDC}")
        sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Only show startup header once (not in the reloader process)
    import os
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        # This is the parent process, show the header
        logger.info(Colors.NEON_GREEN + Colors.BOLD + "\n" + "═" * 80 + Colors.ENDC)
        logger.info("")
        # Cool box-drawing art for PRJCT9
        logger.info(Colors.BRIGHT_GREEN + Colors.BOLD + "    ██████╗ ██████╗      ██╗ ██████╗████████╗ █████╗ ".center(80) + Colors.ENDC)
        logger.info(Colors.BRIGHT_GREEN + Colors.BOLD + "    ██╔══██╗██╔══██╗     ██║██╔════╝╚══██╔══╝██╔══██╗".center(80) + Colors.ENDC)
        logger.info(Colors.NEON_GREEN + Colors.BOLD + "    ██████╔╝██████╔╝     ██║██║        ██║   ╚██████║".center(80) + Colors.ENDC)
        logger.info(Colors.NEON_GREEN + Colors.BOLD + "    ██╔═══╝ ██╔══██╗██   ██║██║        ██║    ╚═══██║".center(80) + Colors.ENDC)
        logger.info(Colors.BRIGHT_GREEN + Colors.BOLD + "    ██║     ██║  ██║╚█████╔╝╚██████╗   ██║    █████╔╝".center(80) + Colors.ENDC)
        logger.info(Colors.BRIGHT_GREEN + Colors.BOLD + "    ╚═╝     ╚═╝  ╚═╝ ╚════╝  ╚═════╝   ╚═╝    ╚════╝ ".center(80) + Colors.ENDC)
        logger.info("")
        logger.info(Colors.YELLOW + "Advanced Trading Analysis Platform".center(80) + Colors.ENDC)
        logger.info(Colors.CYAN + "Built by Rebel Atom LLC".center(80) + Colors.ENDC)
        logger.info("")
        logger.info(Colors.NEON_GREEN + Colors.BOLD + "═" * 80 + "\n" + Colors.ENDC)
        
        log_processing("Starting Dash server...")
        logger.info(f"{Colors.CYAN}[🌐] Server URL: {Colors.YELLOW}http://127.0.0.1:8050{Colors.ENDC}")
        logger.info(f"{Colors.CYAN}[🛑] Stop server: {Colors.YELLOW}Press Ctrl+C{Colors.ENDC}")
        logger.info(Colors.DIM_GREEN + "─" * 80 + Colors.ENDC + "\n")
    
    # Run with debug=False to see console output properly
    # Set debug=True if you need hot reloading
    debug_mode = os.environ.get('DASH_DEBUG', 'False').lower() == 'true'
    
    # Define cleanup function
    def cleanup_server():
        logger.info(f"\n{Colors.YELLOW}[⚡] Shutting down server...{Colors.ENDC}")
        try:
            # Kill any process using port 8050
            if sys.platform == 'win32':
                os.system('netstat -ano | findstr :8050 > temp_port.txt 2>nul')
                try:
                    with open('temp_port.txt', 'r') as f:
                        lines = f.readlines()
                    os.remove('temp_port.txt')
                    for line in lines:
                        if 'LISTENING' in line:
                            parts = line.strip().split()
                            pid = parts[-1]
                            if pid.isdigit():
                                os.system(f'taskkill /F /PID {pid} >nul 2>&1')
                except:
                    pass
            
            # Force terminate all daemon threads
            os._exit(0)
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
        finally:
            logger.info(f"{Colors.GREEN}[✓] Server shutdown complete{Colors.ENDC}")
    
    # Register cleanup handlers
    def signal_handler(signum, frame):
        cleanup_server()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if sys.platform == 'win32':
        signal.signal(signal.SIGBREAK, signal_handler)
    
    atexit.register(cleanup_server)
    
    try:
        # Suppress Flask's startup message
        import click
        import werkzeug
        # Override both click.echo and werkzeug logging
        click.echo = lambda *args, **kwargs: None
        werkzeug._internal._log = lambda *args, **kwargs: None
        
        # Suppress Dash's startup message
        import dash._utils
        dash._utils.print = lambda *args, **kwargs: None
        
        app.run_server(debug=debug_mode, host='127.0.0.1', port=8050, use_reloader=False)
    except KeyboardInterrupt:
        cleanup_server()
    except Exception as e:
        logger.error(f"Server error: {str(e)}")
        cleanup_server()