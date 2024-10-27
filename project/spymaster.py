import yfinance as yf
import plotly.graph_objects as go
from dash import Dash, dcc, html, Input, Output, State, callback_context, no_update, dash_table
import dash_bootstrap_components as dbc
import pandas as pd
from functools import lru_cache
from functools import partial
import pickle
from tqdm import tqdm
import os
os.environ['DASH_CALLBACK_TIMEOUT'] = '300'  # Set timeout to 300 seconds (5 minutes)
import json
import tempfile
import shutil
import time
import numpy as np
import gc
import threading
from threading import Lock
from joblib import Memory
import logging
from tqdm.contrib.logging import logging_redirect_tqdm
import traceback
import random
import glob
from collections import defaultdict

# Initialize the Dash app with a dark theme and custom styles
app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])

master_stopwatch_start = None

# Remove any existing handlers
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

# Create a custom logger for your application
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create handlers
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

file_handler = logging.FileHandler('debug.log')
file_handler.setLevel(logging.DEBUG)

# Create formatters and add them to handlers
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)

file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)

# Add handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Prevent logger from propagating messages to the root logger
logger.propagate = False

# Function to log a separator line
def log_separator():
    logger.info("=" * 80)

# Function to log a section header
def log_section(section_name):
    log_separator()
    logger.info(f" {section_name} ".center(80, "-"))
    log_separator()

# Suppress yfinance debug logs
logging.getLogger('yfinance').setLevel(logging.WARNING)

# Suppress urllib3 debug logs
logging.getLogger('urllib3').setLevel(logging.WARNING)

tqdm.pandas()
MAX_SMA_DAY = 113
_precomputed_results_cache = {}
_loading_in_progress = {}
_loading_lock = threading.Lock()

status_lock = Lock()

# Set up persistent cache
cache_dir = '.cache'
os.makedirs(cache_dir, exist_ok=True)
memory = Memory(cache_dir, verbose=0)

def normalize_ticker(ticker):
    """Normalize ticker to uppercase if it exists"""
    return ticker.strip().upper() if ticker else ticker

def fetch_data(ticker, is_secondary=False):
    try:
        # Check for empty or whitespace-only ticker
        if not ticker or not ticker.strip():
            if not is_secondary:
                logging.info("No primary ticker provided")
            return pd.DataFrame()
            
        # Normalize ticker
        ticker = normalize_ticker(ticker)
            
        df = yf.download(ticker, period='max', interval='1d', progress=False)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        
        # Handle column names properly
        if isinstance(df.columns, pd.MultiIndex):
            # Get the Close prices while maintaining proper column name
            close_data = df['Close'][ticker] if ('Close', ticker) in df.columns else None
            if close_data is not None:
                df = pd.DataFrame({'Close': close_data}, index=df.index)
            else:
                logger.error(f"Could not find Close price data for {ticker}")
                return pd.DataFrame()
        else:
            # For single-level columns, standardize names
            df.columns = [str(col).capitalize() for col in df.columns]
            if 'Close' not in df.columns:
                logger.error(f"Close column not found in single-level columns")
                return pd.DataFrame()
        
        if df.empty:
            logger.error(f"No valid data found for {ticker}")
            return df
        
        if not is_secondary:
            logging.info(f"Successfully fetched primary ticker {ticker} data ({len(df)} periods)")
            # Add a row for the current date if it's not included
            today = pd.Timestamp.now().normalize().tz_localize(None)
            if len(df) > 0 and df.index[-1] < today:  # Check if df has data before accessing index
                last_row = df.iloc[-1].copy()
                last_row.name = today
                df = pd.concat([df, last_row.to_frame().T])
        
        return df
    except Exception as e:
        logging.error(f"Failed to fetch data for '{ticker}': {type(e).__name__} - {str(e)}")
        return pd.DataFrame()

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
            logger.debug(f"Using cached results for {ticker}")
            return _precomputed_results_cache[ticker]

        if ticker in _loading_in_progress:
            logger.debug(f"Loading in progress for {ticker}")
            return None  # Return None immediately if loading is in progress

        # Attempt to load from file if not in cache and not currently loading
        pkl_file = f'{ticker}_precomputed_results.pkl'
        if os.path.exists(pkl_file):
            load_start_time = time.time()
            results = load_precomputed_results_from_file(pkl_file)
            if results:
                if load_full_data:
                    # Load buy and short results incrementally
                    buy_results = {}
                    short_results = {}
                    chunk_files = sorted(glob.glob(f'{ticker}_results_chunk_*.npz'))
                    
                    chunk_load_start = time.time()

                    with tqdm(total=len(chunk_files), desc=f"Loading chunks for {ticker}", unit="chunk", dynamic_ncols=True, mininterval=0.1, leave=True, position=0) as pbar:
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
                logger.debug(f"Loaded results from file for {ticker}")
                return results
            else:
                logger.warning(f"Failed to load results from file for {ticker}")

        logger.info(f"Starting to load precomputed results for {ticker.upper()}...")
        event = threading.Event()
        _loading_in_progress[ticker] = event
        threading.Thread(target=precompute_results, args=(ticker, event)).start()
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
    logging.info(f"get_data called for {ticker} with MAX_SMA_DAY {MAX_SMA_DAY}")
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
    status_path = f"{ticker}_status.json"
    with status_lock:
        with open(status_path, 'w') as f:
            json.dump(status, f)

def save_precomputed_results(ticker, results):
    ticker = normalize_ticker(ticker)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = tempfile.gettempdir()
    temp_file_path = os.path.join(temp_dir, f'{ticker}_precomputed_results_temp.pkl')
    final_file_path = os.path.join(current_dir, f'{ticker}_precomputed_results.pkl')

    try:
        # Save main results (excluding buy_results and short_results)
        main_results = {k: v for k, v in results.items() if k not in ['buy_results', 'short_results']}
        
        # Ensure daily_top_buy_pairs and daily_top_short_pairs are included
        if 'daily_top_buy_pairs' not in main_results or 'daily_top_short_pairs' not in main_results:
            logging.warning("daily_top_pairs not found in results, recalculating...")
            daily_top_buy_pairs, daily_top_short_pairs = calculate_daily_top_pairs(
                results['preprocessed_data'], ticker)
            main_results['daily_top_buy_pairs'] = daily_top_buy_pairs
            main_results['daily_top_short_pairs'] = daily_top_short_pairs
        
        with open(temp_file_path, 'wb') as f:
            pickle.dump(main_results, f)

        # Atomically move the temp file to the final destination
        shutil.move(temp_file_path, final_file_path)
        logging.info(f"Main results saved successfully to {final_file_path}")
        logging.info(f"Number of daily_top_buy_pairs saved: {len(main_results.get('daily_top_buy_pairs', {}))}")
        logging.info(f"Number of daily_top_short_pairs saved: {len(main_results.get('daily_top_short_pairs', {}))}")

        # Since buy_results and short_results are saved in chunks during processing,
        # we no longer need to save them here.

        logging.info(f"All results saved successfully for {ticker}")
    except PermissionError:
        logging.error(f"Permission denied when saving results to {final_file_path}. Please check file permissions.")
    except Exception as e:
        logging.error(f"Error saving results for {ticker}: {str(e)}")
        logging.error(traceback.format_exc())

    # Return the main_results even if an exception occurred
    return main_results

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

def calculate_daily_top_pairs(df, ticker):
    log_section("Daily Top Pairs Calculation")
    section_start = time.time()
    logger.info("Calculating daily top pairs...")
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
    with tqdm(total=len(chunk_files), desc="Processing chunks for daily top pairs", unit="chunk", dynamic_ncols=True, mininterval=0.1, leave=True, position=0) as pbar:
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
    logger.info(f"Number of daily top buy pairs: {len(max_buy_pairs_global)}")
    logger.info(f"Number of daily top short pairs: {len(max_short_pairs_global)}")

    total_chunk_processing_time = sum(chunk_processing_times)
    logger.info(f"Total time for processing chunks: {total_chunk_processing_time:.2f} seconds")

    section_time = time.time() - section_start
    logger.info(f"Total time for Daily Top Pairs Calculation: {section_time:.2f} seconds")

    return daily_top_buy_pairs, daily_top_short_pairs, total_chunk_processing_time

def calculate_captures_vectorized(sma1, sma2, returns):
    buy_signals = (sma1 > sma2)
    short_signals = (sma1 < sma2)

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

def save_precomputed_results_chunk(ticker, buy_results_chunk, short_results_chunk, chunk_index):
    ticker = normalize_ticker(ticker)
    chunk_file = f'{ticker}_results_chunk_{chunk_index}.npz'
    try:
        # Prepare data for saving
        buy_pairs = np.array(list(buy_results_chunk.keys()))
        buy_values = np.array(list(buy_results_chunk.values()))
        short_pairs = np.array(list(short_results_chunk.keys()))
        short_values = np.array(list(short_results_chunk.values()))
        
        logger.info(f"Saving chunk {chunk_index}...")

        # Save the chunk synchronously
        np.savez(chunk_file, buy_pairs=buy_pairs, buy_values=buy_values,
                 short_pairs=short_pairs, short_values=short_values)
        
        file_size = os.path.getsize(chunk_file) / (1024 * 1024 * 1024)  # Size in GB
        logger.info(f"Chunk {chunk_index} saved successfully to {chunk_file} (Size: {file_size:.2f} GB)")
    except Exception as e:
        logger.error(f"Error saving results for {ticker}: {str(e)}")
        logger.error(traceback.format_exc())

def precompute_results(ticker, event):
    global master_stopwatch_start
    master_stopwatch_start = time.time()
    section_times = {}
    global _loading_in_progress, _precomputed_results_cache
    with logging_redirect_tqdm():
        try:
            logger.info(f"precompute_results called for {ticker.upper()}")
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
            
            log_section("Data Preprocessing")
            logger.info(f"Data fetched and preprocessed for {ticker.upper()}")
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

            MAX_TRADING_DAYS = 30000  # Adjust this value based on your system's capabilities
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
                with _loading_lock:
                    _precomputed_results_cache[ticker] = existing_results
                return existing_results
            
            results = existing_results or {}
            
            start_date = df.index.min().strftime('%Y-%m-%d')
            last_date = df.index.max().strftime('%Y-%m-%d')
            logger.info(f"Date range: {start_date} to {last_date}")

            # Begin SMA Calculation
            log_section("SMA Calculation")
            logger.info("Calculating new SMA columns...")

            # Prepare a list to store SMA DataFrames
            sma_list = []

            logger.info("Beginning SMA calculations in chunks...")
            chunk_size_sma = 50  # Adjust based on memory constraints
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
                    
            logger.info(f"Completed SMA calculations for {max_sma_day - existing_max_sma_day} new periods")

            # Concatenate all SMA chunks and add to the DataFrame at once
            sma_df = pd.concat(sma_list, axis=1)
            df = pd.concat([df, sma_df], axis=1)
            df = df.copy()  # De-fragment the DataFrame

            logger.info(f"Added {max_sma_day - existing_max_sma_day} new SMA columns to DataFrame.")

            # Ensure NaN values for the first j-1 rows of each SMA column
            for j in range(1, max_sma_day + 1):
                df.iloc[:j-1, df.columns.get_loc(f'SMA_{j}')] = np.nan

            logger.info("Ensured correct NaN values for SMA calculations.")

            # Pre-calculate new SMAs
            sma_columns = {}
            for i in range(existing_max_sma_day + 1, max_sma_day + 1):
                sma_columns[i] = df['Close'].rolling(window=i).mean().values

            logger.info("Generating new SMA pairs...")
            start_time = time.time()
            new_sma_pairs = [
                (i, j) for i in range(existing_max_sma_day + 1, max_sma_day)
                for j in range(i + 1, max_sma_day + 1)
            ]
            total_pairs = len(new_sma_pairs)
            total_pairs_with_inverses = total_pairs * 2
            logger.info(f"Generated {total_pairs} unique SMA pairs (including inverses: {total_pairs_with_inverses}) in {time.time() - start_time:.2f} seconds")

            returns = df['Close'].pct_change().values

            # Begin Capture Calculation
            log_section("Capture Calculation")
            chunk_size = 100000  # Adjust based on your system's capabilities
            update_interval = 100  # Update progress more frequently

            with tqdm(total=total_pairs_with_inverses, desc=f'Calculation for {ticker.upper()}', unit='pair', dynamic_ncols=True, mininterval=0.1, leave=True, position=0) as pbar_calc:
                for i in range(0, total_pairs, chunk_size):
                    chunk_pairs = new_sma_pairs[i:i+chunk_size]

                    sma1_array = np.array([sma_columns[pair[0]] for pair in chunk_pairs])
                    sma2_array = np.array([sma_columns[pair[1]] for pair in chunk_pairs])

                    buy_captures, short_captures = calculate_captures_vectorized(sma1_array, sma2_array, returns)

                    # Initialize chunk dictionaries
                    buy_results_chunk = {}
                    short_results_chunk = {}

                    # Process original pairs
                    buy_results_chunk.update(zip(chunk_pairs, buy_captures))
                    short_results_chunk.update(zip(chunk_pairs, short_captures))

                    # Process inverse pairs
                    inverse_pairs = [(pair[1], pair[0]) for pair in chunk_pairs]
                    inverse_buy_captures = -short_captures
                    inverse_short_captures = -buy_captures

                    buy_results_chunk.update(zip(inverse_pairs, inverse_buy_captures))
                    short_results_chunk.update(zip(inverse_pairs, inverse_short_captures))

                    # Update progress bar
                    pbar_calc.update(len(chunk_pairs) * 2)

                    # Update status
                    pairs_processed = min(i + chunk_size, total_pairs) * 2
                    progress_percentage = pairs_processed / total_pairs_with_inverses * 100
                    write_status(ticker, {"status": "processing", "progress": progress_percentage})

                    # Save results after processing each chunk (synchronously)
                    tqdm.write(f"Saving chunk {i // chunk_size}...")
                    save_precomputed_results_chunk(ticker, buy_results_chunk, short_results_chunk, i // chunk_size)
                    tqdm.write(f"Chunk {i // chunk_size} saved successfully.")

                    # Clear the chunk dictionaries to free up memory
                    buy_results_chunk.clear()
                    short_results_chunk.clear()
                    gc.collect()
                    tqdm.write("Garbage collection completed.")

                    # Update the progress bar after processing the chunk
                    pbar_calc.update(len(chunk_pairs) * 2)

            logger.info(f"Processed {pairs_processed} pairs out of {total_pairs_with_inverses} for {ticker.upper()}")
            logger.info(f"Total buy pairs: {total_pairs * 2}, Total short pairs: {total_pairs * 2}")

            # Update other results
            results['preprocessed_data'] = df
            results['existing_max_sma_day'] = max_sma_day
            results['last_processed_date'] = df.index[-1]
            results['start_date'] = start_date
            results['last_date'] = last_date
            results['total_trading_days'] = total_trading_days

            # Begin Daily Top Pairs Calculation
            log_section("Daily Top Pairs Calculation")
            section_start = log_section_time("Daily Top Pairs Calculation")
            daily_top_buy_pairs, daily_top_short_pairs, chunk_processing_time = calculate_daily_top_pairs(df, ticker)
            results['chunk_processing_time'] = chunk_processing_time
            
            results['daily_top_buy_pairs'] = daily_top_buy_pairs
            results['daily_top_short_pairs'] = daily_top_short_pairs

            # Save the results after calculating daily top pairs
            save_precomputed_results(ticker, results)

            # Begin Cumulative Combined Captures Calculation
            log_section("Cumulative Combined Captures")
            section_start = log_section_time("Cumulative Combined Captures")
            cumulative_combined_captures, active_pairs = calculate_cumulative_combined_capture(df, daily_top_buy_pairs, daily_top_short_pairs)

            results['cumulative_combined_captures'] = cumulative_combined_captures
            results['active_pairs'] = active_pairs

            # Load buy and short results from chunk files
            buy_results = {}
            short_results = {}
            chunk_files = sorted(glob.glob(f'{ticker}_results_chunk_*.npz'))

            with tqdm(total=len(chunk_files), desc=f"Loading chunks for {ticker}", unit="chunk") as pbar:
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

            # Calculate top pairs
            buy_results_with_inverse = {}
            short_results_with_inverse = {}

            for pair, result in buy_results.items():
                buy_results_with_inverse[pair] = result[-1]
                inverse_pair = (pair[1], pair[0])
                short_results_with_inverse[inverse_pair] = -result[-1]

            for pair, result in short_results.items():
                short_results_with_inverse[pair] = result[-1]
                inverse_pair = (pair[1], pair[0])
                buy_results_with_inverse[inverse_pair] = -result[-1]

            # Identify the top performing buy and short pairs from the respective dictionaries
            top_buy_pair = max(buy_results_with_inverse, key=buy_results_with_inverse.get)
            top_short_pair = max(short_results_with_inverse, key=short_results_with_inverse.get)

            # Calculate the total capture for the top pairs
            top_buy_capture = buy_results_with_inverse[top_buy_pair]
            top_short_capture = short_results_with_inverse[top_short_pair]

            # Print the top pairs along with their results          
            logger.info(f"Current Top Buy Pair for {ticker.upper()}: {top_buy_pair} with total capture {top_buy_capture:.6f}")
            logger.info(f"Current Top Short Pair for {ticker.upper()}: {top_short_pair} with total capture {top_short_capture:.6f}")

            # Update the results dictionary with the latest total captures
            results['top_buy_pair'] = top_buy_pair
            results['top_buy_capture'] = top_buy_capture
            results['top_short_pair'] = top_short_pair
            results['top_short_capture'] = top_short_capture

            # Save final results
            logger.info(f"Saving final results to {pkl_file}")
            with tqdm(total=1, desc="Saving final results", unit="file", leave=True, position=0) as pbar_save:
                save_precomputed_results(ticker, results)
                pbar_save.update(1)
            write_status(ticker, {"status": "complete", "progress": 100})

            logger.info("Process completed.")

            # Update the cache
            with _loading_lock:
                _precomputed_results_cache[ticker] = results
                # Signal that loading is complete
                if ticker in _loading_in_progress:
                    _loading_in_progress[ticker].set()
                    del _loading_in_progress[ticker]

            # Force an update of the Dash app
            logger.info("Updating Dash app layout...")
            app.layout = app.layout
            logger.info("Dash app layout updated.")

            # Store timing information for later use
            results['section_times'] = section_times
            results['start_time'] = master_stopwatch_start

        except Exception as e:
            # Handle exceptions
            logger.error(f"Error in precompute_results for {ticker}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            # Ensure the event is set even if an error occurs
            with _loading_lock:
                if ticker in _loading_in_progress:
                    _loading_in_progress[ticker].set()
                    del _loading_in_progress[ticker]

    # Print final message outside of the try-except block
    logger.info("Computation and loading process completed.")

def print_timing_summary(ticker):
    results = _precomputed_results_cache.get(ticker)
    if results and 'section_times' in results and 'start_time' in results:
        section_times = results['section_times']
        start_time = results['start_time']
        
        total_time = time.time() - start_time
        hours, rem = divmod(total_time, 3600)
        minutes, seconds = divmod(rem, 60)
        
        logger.info("=" * 80)
        logger.info("Processing Time Summary:")
        logger.info("=" * 80)
        for section, time_taken in section_times.items():
            logger.info(f"{section}: {time_taken:.2f} seconds")
        
        if 'chunk_processing_time' in results:
            logger.info(f"Daily Top Pairs Chunk Processing: {results['chunk_processing_time']:.2f} seconds")
        
        logger.info("=" * 80)
        logger.info(f"Total processing time for {ticker.upper()}: {int(hours):02d}:{int(minutes):02d}:{int(seconds):02d} (hh:mm:ss)")
        logger.info("=" * 80)
        logger.info("Load complete. Data is now available in the Dash app.")
    elif results and 'load_time' in results:
        load_time = results['load_time']
        hours, rem = divmod(load_time, 3600)
        minutes, seconds = divmod(rem, 60)
        logger.info("=" * 80)
        logger.info(f"Loading time for existing {ticker.upper()} data: {int(hours):02d}:{int(minutes):02d}:{int(seconds):02d} (hh:mm:ss)")
        logger.info("=" * 80)
        logger.info("Load complete. Data is now available in the Dash app.")

# Function to read the processing status from a file
def read_status(ticker):
    ticker = normalize_ticker(ticker)
    status_path = f"{ticker}_status.json"
    if os.path.exists(status_path):
        with open(status_path, 'r') as file:
            try:
                return json.load(file)
            except json.JSONDecodeError:
                print(f"Empty JSON file: {status_path}")
    return {"status": "not started", "progress": 0}

status = read_status('AAPL')
print(status)

def inspect_pkl_file(ticker, sample_size=5):
    pkl_file = f'{ticker}_precomputed_results.pkl'
    if os.path.exists(pkl_file):
        with open(pkl_file, 'rb') as f:
            results = pickle.load(f)
        keys = list(results.keys())
        sample_keys = random.sample(keys, min(sample_size, len(keys)))
        print(f"Sample keys in {pkl_file}: {sample_keys}")
    else:
        print(f"{pkl_file} does not exist.")

# Optionally, call it manually for a specific ticker
# inspect_pkl_file('VIK')

app.layout = html.Div(
    style={
        'background-color': 'black',
        'color': '#80ff00',
        'font-family': 'Impact, sans-serif'
    },
    children=[
        html.H1('Adaptive Simple Moving Average Pair Optimization and Mean Reversion-Based Systematic Trading Framework', className='text-center mt-3'),
        html.Div(id='max-sma-day-display', style={'font-size': '18px', 'margin-bottom': '20px'}),
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader('Select Primary Ticker Symbol (Signal Generator)'),
                    dbc.CardBody([
                        dbc.Input(id='ticker-input', placeholder='Enter a valid ticker symbol (e.g., AAPL)', type='text', debounce=True),
                        dbc.FormFeedback(id='ticker-input-feedback', className='text-danger')
                    ])
                ], className='mb-3')
            ], width=12)
        ]),
        dcc.Store(id='timing-summary-printed', data=False),
        dbc.Row([
            dbc.Col([
                dcc.Loading(
                    id="loading-combined-capture",
                    type="default",
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
        dbc.Row([
            dbc.Col([
                dcc.Loading(
                    id="loading-historical-top-pairs",
                    type="default",
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
        dbc.Row([
            dbc.Col([
                dcc.Graph(id='chart')
            ], width=12)
        ]),
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader('Buy Pair'),
                    dbc.CardBody([
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
                    dbc.CardHeader('Short Pair'),
                    dbc.CardBody([
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
                        html.H5('Manual Calculation Components', className='mb-0'),
                        html.Button(children='Maximize', id='toggle-calc-button', className='btn btn-sm btn-secondary ml-auto')
                    ]),
                    dbc.Collapse(
                        dbc.CardBody([
                            html.H6('Buy Pair', className='mt-3'),
                            html.Div(id='trigger-days-buy'),
                            html.Div(id='win-ratio-buy'),
                            html.Div(id='avg-daily-capture-buy'),
                            html.Div(id='total-capture-buy'),
                            html.H6('Short Pair', className='mt-3'),
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
                            html.Div(id='processing-status')  # For showing processing status
                        ]),
                        id='strategy-collapse',
                        is_open=False
                    )
                ])
            ], width=12)
        ]),
        # Existing loading spinner (keep if necessary)
        # If you already have a loading spinner elsewhere, you can remove this or ensure IDs are unique
        # dcc.Loading(
        #     id="loading-spinner",
        #     type="default",
        #     children=[html.Div(id="loading-spinner-output")]
        # ),
        # Secondary Ticker Input and Chart Section
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader('Select Secondary Ticker Symbol(s)'),
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
                    type="default",
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
                            dbc.CardHeader('Signal Following Metrics'),
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
        # Add intervals at the end
        dcc.Interval(id='update-interval', interval=5000, n_intervals=0, disabled=False),  # Decreased to 5 seconds from 30 seconds
        dcc.Interval(id='loading-interval', interval=2000, n_intervals=0),  # Update every 2 seconds
        # Loading spinner output (if needed)
        dcc.Loading(
            id="loading-spinner",
            type="default",
            children=[html.Div(id="loading-spinner-output")]
        ),
    ]
)

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
    return f"Current MAX_SMA_DAY for {ticker}: {MAX_SMA_DAY}"

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
        f"Enter 3rd SMA Day (1-{trading_days}) for Short Pair:",
        f"Enter 4th SMA Day (1-{trading_days}) for Short Pair:"
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
    # Assuming the SMA columns in df are named 'SMA_X' where X is the SMA day
    sma_columns = [col for col in df.columns if 'SMA_' in col]
    
    if not sma_columns:
        return 0
    
    # Extract the SMA day from each column name and convert to int
    sma_days = [int(col.split('_')[1]) for col in sma_columns]
    
    # Return the maximum SMA day
    return max(sma_days)

@app.callback(
    Output('ticker-input-feedback', 'children'),
    [Input('ticker-input', 'value')]
)
def validate_ticker_input(ticker):
    global MAX_SMA_DAY
    if not ticker:
        return ''

    results = get_data(ticker, MAX_SMA_DAY)
    if results is None:
        return 'Error retrieving data. Please check the console for more information.'

    return ''

def calculate_cumulative_combined_capture(df, daily_top_buy_pairs, daily_top_short_pairs):
    log_separator()
    logger.info("Calculating cumulative combined capture")
    logger.info(f"Number of trading days: {len(df)}")

    if not daily_top_buy_pairs or not daily_top_short_pairs:
        logger.warning("No daily top pairs available for processing cumulative combined captures.")
        return pd.Series([0], index=[df.index[0]]), ['None']

    dates = sorted(set(daily_top_buy_pairs.keys()) | set(daily_top_short_pairs.keys()))
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

    # After the loop, print a summary
    logger.info("Cumulative Capture Summary:")
    logger.info(f"Date range: {dates[0]} to {dates[-1]}")
    logger.info(f"Total Trading Days: {len(dates)}")
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
        print("Calculated new cumulative_combined_captures and active_pairs")

        # Update the results dictionary with the new data
        results['cumulative_combined_captures'] = cumulative_combined_captures
        results['active_pairs'] = active_pairs
        save_precomputed_results(ticker, results)

    print(f"Number of cumulative combined captures: {len(cumulative_combined_captures)}")
    print(f"Number of active pairs: {len(active_pairs)}")

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
    
    # Ensure 'preprocessed_data' exists
    if 'preprocessed_data' not in results:
        print("Error: 'preprocessed_data' key missing in results.")
        return None, None, None, None, None, None
    
    df = results['preprocessed_data']
    daily_top_buy_pairs = results.get('daily_top_buy_pairs', {})
    daily_top_short_pairs = results.get('daily_top_short_pairs', {})
    cumulative_combined_captures = results.get('cumulative_combined_captures', pd.Series())
    active_pairs = results.get('active_pairs', [])
    
    logger.info(f"Loaded preprocessed_data with {len(df)} trading days.")
    
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
            f"SMA {daily_top_buy_pairs[date][0][0]} / SMA {daily_top_buy_pairs[date][0][1]} ({daily_top_buy_pairs[date][1] * 100:.2f}%)"
            if isinstance(daily_top_buy_pairs[date][0], tuple) else
            f"SMA {daily_top_buy_pairs[date][0]} / SMA {daily_top_buy_pairs[date][1]} ({daily_top_buy_pairs[date][1] * 100:.2f}%)"
            for date in cumulative_combined_captures.index
        ],
        'top_short_pair': [
            f"SMA {daily_top_short_pairs[date][0][0]} / SMA {daily_top_short_pairs[date][0][1]} ({daily_top_short_pairs[date][1] * 100:.2f}%)"
            if isinstance(daily_top_short_pairs[date][0], tuple) else
            f"SMA {daily_top_short_pairs[date][0]} / SMA {daily_top_short_pairs[date][1]} ({daily_top_short_pairs[date][1] * 100:.2f}%)"
            for date in cumulative_combined_captures.index
        ],
        'active_pair_current': active_pairs,
        'active_pair_next': active_pairs[1:] + ['']  # Placeholder for the last day
    })

    # Calculate the next day's active pair for the last day
    last_date = data['date'].iloc[-1]
    top_buy_pair = daily_top_buy_pairs.get(last_date, ((0, 0), 0))[0]
    top_short_pair = daily_top_short_pairs.get(last_date, ((0, 0), 0))[0]

    if top_buy_pair[0] != 0 and top_buy_pair[1] != 0 and top_short_pair[0] != 0 and top_short_pair[1] != 0:
        buy_signal = df[f'SMA_{top_buy_pair[0]}'].iloc[-1] > df[f'SMA_{top_buy_pair[1]}'].iloc[-1]
        short_signal = df[f'SMA_{top_short_pair[0]}'].iloc[-1] < df[f'SMA_{top_short_pair[1]}'].iloc[-1]
        
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

    print(f"Sample data rows:\n{data.head()}\n{data.tail()}")

    # Calculate the active pair for the upcoming trading session
    last_date = df.index[-1]
    top_buy_pair = daily_top_buy_pairs[last_date][0]
    top_short_pair = daily_top_short_pairs[last_date][0]

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

    logger.info(f"Active Pair for Upcoming Trading Session: {next_active_pair}")
    log_separator()

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

        # Extract required data from results
        df = results['preprocessed_data']
        daily_top_buy_pairs = results['daily_top_buy_pairs']
        daily_top_short_pairs = results['daily_top_short_pairs']
        cumulative_combined_captures = results['cumulative_combined_captures']
        active_pairs = results['active_pairs']

        logger.info(f"Number of trading days: {len(df)}")
        logger.info(f"Number of daily top buy pairs: {len(daily_top_buy_pairs)}")
        logger.info(f"Number of daily top short pairs: {len(daily_top_short_pairs)}")

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

        # The rest of your existing code remains the same
        # Add the combined capture trace and annotations as before

        # Colors for the combined capture trace based on active pairs
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

        # Calculate the next day's active pair for the last day
        last_date = cumulative_combined_captures.index[-1]
        top_buy_pair = daily_top_buy_pairs.get(last_date, ((0, 0), 0))[0]
        top_short_pair = daily_top_short_pairs.get(last_date, ((0, 0), 0))[0]

        if top_buy_pair != (0, 0):
            sma_long = df[f'SMA_{top_buy_pair[0]}'].iloc[-1]
            sma_short = df[f'SMA_{top_buy_pair[1]}'].iloc[-1]
            if sma_long > sma_short:
                next_day_pairs[-1] = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
            elif sma_long < sma_short:
                next_day_pairs[-1] = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
            else:
                next_day_pairs[-1] = "None"
        else:
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

    df = results.get('preprocessed_data')
    if df is None or df.empty:
        logger.warning(f"Warning: 'preprocessed_data' is missing or empty for {ticker}")
        return ["Data integrity issue. Please check the precomputed results."] * 10

    if top_buy_pair is None or top_short_pair is None:
        return ["Data integrity issue. Please check the precomputed results."] * 10

    try:
        sma1_buy_leader = df[f'SMA_{top_buy_pair[0]}']
        sma2_buy_leader = df[f'SMA_{top_buy_pair[1]}']
        buy_signals_leader = sma1_buy_leader > sma2_buy_leader
        close_pct_change = df['Close'].pct_change().values

        sma1_short_leader = df[f'SMA_{top_short_pair[0]}']
        sma2_short_leader = df[f'SMA_{top_short_pair[1]}']
        short_signals_leader = sma1_short_leader < sma2_short_leader

    except KeyError:
        logger.error(f"Required SMA columns not found in the DataFrame for {ticker}")
        return ["Data not available or processing not yet complete. Please wait..."] * 10

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

    # Calculate signals for today based on yesterday's close
    buy_signal = sma1_buy_leader.loc[previous_date] > sma2_buy_leader.loc[previous_date]
    short_signal = sma1_short_leader.loc[previous_date] < sma2_short_leader.loc[previous_date]

    # Calculate signals for tomorrow based on today's close
    next_buy_signal = sma1_buy_leader.loc[current_date] > sma2_buy_leader.loc[current_date]
    next_short_signal = sma1_short_leader.loc[current_date] < sma2_short_leader.loc[current_date]

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

    # Calculate buy returns on days when buy signal was active
    buy_signals_shifted = buy_signals_leader.shift(1, fill_value=False)
    buy_returns_on_trigger_days = close_pct_change[buy_signals_shifted]
    buy_trigger_days = np.sum(buy_signals_shifted)

    # Calculate wins and losses for buy signals
    buy_wins = np.sum(buy_returns_on_trigger_days > 0)
    buy_losses = np.sum(buy_returns_on_trigger_days <= 0)
    buy_win_ratio = buy_wins / buy_trigger_days if buy_trigger_days > 0 else 0
    avg_capture_buy = np.mean(buy_returns_on_trigger_days) if buy_trigger_days > 0 else 0
    buy_capture = np.sum(buy_returns_on_trigger_days) * 100 if buy_trigger_days > 0 else 0

    # Calculate short returns on days when short signal was active
    short_signals_shifted = short_signals_leader.shift(1, fill_value=False)
    short_returns_on_trigger_days = -close_pct_change[short_signals_shifted]
    short_trigger_days = np.sum(short_signals_shifted)

    # Calculate wins and losses for short signals
    short_wins = np.sum(short_returns_on_trigger_days > 0)
    short_losses = np.sum(short_returns_on_trigger_days <= 0)
    short_win_ratio = short_wins / short_trigger_days if short_trigger_days > 0 else 0
    avg_capture_short = np.mean(short_returns_on_trigger_days) if short_trigger_days > 0 else 0
    short_capture = np.sum(short_returns_on_trigger_days) * 100 if short_trigger_days > 0 else 0

    avg_capture_buy_leader = (
        f"Avg. Daily Capture % for Buy Leader: {avg_capture_buy * 100:.4f}% "
        f"(Trigger Days: {buy_trigger_days}, Wins: {buy_wins}, Losses: {buy_losses}, Win Ratio: {buy_win_ratio * 100:.2f}%)"
    )

    avg_capture_short_leader = (
        f"Avg. Daily Capture % for Short Leader: {avg_capture_short * 100:.4f}% "
        f"(Trigger Days: {short_trigger_days}, Wins: {short_wins}, Losses: {short_losses}, Win Ratio: {short_win_ratio * 100:.2f}%)"
    )

    total_capture_buy_leader = f"Total Capture for Buy Leader: {buy_capture:.4f}%"
    total_capture_short_leader = f"Total Capture for Short Leader: {short_capture:.4f}%"

    # Calculate combined strategy performance
    daily_top_buy_pairs = results.get('daily_top_buy_pairs', {})
    daily_top_short_pairs = results.get('daily_top_short_pairs', {})

    combined_returns = []
    combined_trigger_days = 0
    combined_wins = 0
    combined_losses = 0

    for date in df.index[1:]:  # Start from the second day to calculate returns
        prev_date = df.index[df.index < date][-1]  # Get the previous trading day
        
        buy_pair, buy_capture = daily_top_buy_pairs.get(prev_date, ((0, 0), 0))
        short_pair, short_capture = daily_top_short_pairs.get(prev_date, ((0, 0), 0))
        
        if buy_pair != (0, 0) and short_pair != (0, 0):
            buy_signal = df[f'SMA_{buy_pair[0]}'].loc[prev_date] > df[f'SMA_{buy_pair[1]}'].loc[prev_date]
            short_signal = df[f'SMA_{short_pair[0]}'].loc[prev_date] < df[f'SMA_{short_pair[1]}'].loc[prev_date]
            
            daily_return = df['Close'].loc[date] / df['Close'].loc[prev_date] - 1
            
            if buy_signal and short_signal:
                if buy_capture > short_capture:
                    combined_return = daily_return
                else:
                    combined_return = -daily_return
            elif buy_signal:
                combined_return = daily_return
            elif short_signal:
                combined_return = -daily_return
            else:
                combined_return = 0
            
            if combined_return != 0:
                combined_trigger_days += 1
                if combined_return > 0:
                    combined_wins += 1
                else:
                    combined_losses += 1
            
            combined_returns.append(combined_return)

    combined_returns = np.array(combined_returns)
    combined_total_capture = np.sum(combined_returns) * 100
    combined_win_ratio = combined_wins / combined_trigger_days if combined_trigger_days > 0 else 0
    combined_avg_capture = np.mean(combined_returns) * 100 if combined_trigger_days > 0 else 0

    combined_strategy_text = f"""
    Combined Strategy Performance:
    Total Capture: {combined_total_capture:.4f}%
    Avg. Daily Capture: {combined_avg_capture:.4f}%
    Win Ratio: {combined_win_ratio * 100:.2f}%
    Trigger Days: {combined_trigger_days}
    Wins: {combined_wins}, Losses: {combined_losses}
    """

    # Performance expectation (using the next trading signal)
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
            return None  # Cannot compute crossing price when periods are equal
        # Ensure there is enough data
        min_length = max(n1, n2)
        if len(df) < min_length:
            return None  # Not enough data to compute SMAs
        # Sum of the previous (n1 - 1) closing prices, excluding the current price
        sum1 = df['Close'].iloc[-(n1):-1].sum()
        sum2 = df['Close'].iloc[-(n2):-1].sum()
        numerator = n1 * sum2 - n2 * sum1
        denominator = n2 - n1
        if denominator == 0:
            return None
        crossing_price = numerator / denominator
        return crossing_price if crossing_price > 0 and np.isfinite(crossing_price) else None

    # Calculate crossing prices
    crossing_price_buy = find_crossing_price(top_buy_pair[0], top_buy_pair[1])
    crossing_price_short = find_crossing_price(top_short_pair[0], top_short_pair[1])

    # Get current price and set a reasonable upper bound
    current_price = df['Close'].iloc[-1]
    max_price = current_price * 1.5  # Adjust this multiplier as needed

    # Create price points
    price_points = []
    if crossing_price_buy is not None and crossing_price_buy > 0:
        price_points.append(crossing_price_buy)
    if crossing_price_short is not None and crossing_price_short > 0:
        price_points.append(crossing_price_short)
    # Include the current price
    price_points.append(current_price)
    # Remove duplicates and sort
    price_points = sorted(set(price_points))
    # Ensure 0 is included if not already
    if 0 not in price_points:
        price_points.insert(0, 0)
    # Add a reasonable upper bound
    price_points.append(max_price)

    price_ranges = []
    for i in range(len(price_points) - 1):
        low = price_points[i]
        high = price_points[i + 1]
        if high > low:
            price_ranges.append({'low': low, 'high': high})
    # Add the last range if needed
    if price_points[-1] < float('inf'):
        price_ranges.append({'low': price_points[-1], 'high': float('inf')})

    # Predict signals for each price range
    predictions = []
    for pr in price_ranges:
        low = pr['low']
        high = pr['high']
        # Choose a sample price slightly above the low to avoid edge cases
        sample_price = low + (high - low) * 0.01 if high != float('inf') else low * 1.01
        signal, active_pair = predict_signal(sample_price)
        recommendations = {
            'Buy': 'Enter Buy Position',
            'Short': 'Enter Short Position',
            'Cash': 'Hold Cash Position'
        }
        recommendation = recommendations.get(signal, 'Hold Cash Position')
        price_range_str = f"${low:.2f} - ${high:.2f}" if high != float('inf') else f"${low:.2f} and above"
        predictions.append({
            'price_range': price_range_str,
            'signal': signal,
            'active_pair': active_pair,
            'recommendation': recommendation
        })

    # Log the predictions for debugging
    logger.info("Forecast Recommendations:")
    for pred in predictions:
        logger.info(f"Range: {pred['price_range']}, Signal: {pred['signal']}, Pair: {pred['active_pair']}, Recommendation: {pred['recommendation']}")

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
                html.P(f"Average Daily Capture (%): {avg_capture_buy * 100:.4f}%", className="mb-1"),
                html.P(f"Total Capture (%): {buy_capture:.4f}%", className="mb-1"),
                html.P(f"Trigger Days: {int(buy_trigger_days):,}", className="mb-1"),
                html.P(f"Wins: {int(buy_wins):,}", className="mb-1"),
                html.P(f"Losses: {int(buy_losses):,}", className="mb-1"),
                html.P(f"Win Ratio: {buy_win_ratio * 100:.2f}%", className="mb-3"),
                
                html.H5("Short Leader Performance:", className="mb-2"),
                html.P(f"Average Daily Capture (%): {avg_capture_short * 100:.4f}%", className="mb-1"),
                html.P(f"Total Capture (%): {short_capture:.4f}%", className="mb-1"),
                html.P(f"Trigger Days: {int(short_trigger_days):,}", className="mb-1"),
                html.P(f"Wins: {int(short_wins):,}", className="mb-1"),
                html.P(f"Losses: {int(short_losses):,}", className="mb-1"),
                html.P(f"Win Ratio: {short_win_ratio * 100:.2f}%", className="mb-1"),
            ], className="mb-4"),
            
            html.Div([
                html.H4("3. Trading Signals", className="mb-3"),
                html.P(f"Current Trading Signal ({current_date.strftime('%Y-%m-%d')}): {trading_signal_type} "
                       f"(SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]})" if trading_signal_type == "Buy" else 
                       f"(SMA {top_short_pair[0]} / SMA {top_short_pair[1]})", className="mb-2"),
                html.P(f"Next Trading Signal ({next_trading_day.strftime('%Y-%m-%d')}): {next_trading_signal_type} "
                       f"(SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]})" if next_trading_signal_type == "Buy" else 
                       f"(SMA {top_short_pair[0]} / SMA {top_short_pair[1]})", className="mb-2"),
            ], className="mb-4"),
            
            html.Div([
                html.H4("4. Combined Strategy Performance", className="mb-3"),
                html.P(f"Total Capture (%): {combined_total_capture:.4f}%", className="mb-1"),
                html.P(f"Average Daily Capture (%): {combined_avg_capture:.4f}%", className="mb-1"),
                html.P(f"Trigger Days: {combined_trigger_days:,}", className="mb-1"),
                html.P(f"Wins: {combined_wins:,}", className="mb-1"),
                html.P(f"Losses: {combined_losses:,}", className="mb-1"),
                html.P(f"Win Ratio: {combined_win_ratio * 100:.2f}%", className="mb-1"),
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
     Output('total-capture-short', 'children')],
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
        return empty_fig, '', '', '', '', '', '', '', ''

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
        return empty_fig, '', '', '', '', '', '', '', ''
        
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
            yaxis_title=f'{ticker} Closing Price',
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
        return fig, '', '', '', '', '', '', '', ''

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

    return fig, trigger_days_buy, win_ratio_buy, avg_daily_capture_buy, total_capture_buy, trigger_days_short, win_ratio_short, avg_daily_capture_short, total_capture_short

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
    
def print_timing_summary(ticker):
    results = _precomputed_results_cache.get(ticker)
    if results and 'section_times' in results and 'start_time' in results:
        section_times = results['section_times']
        start_time = results['start_time']
        
        total_time = time.time() - start_time
        hours, rem = divmod(total_time, 3600)
        minutes, seconds = divmod(rem, 60)
        
        logger.info("=" * 80)
        logger.info("Processing Time Summary:")
        logger.info("=" * 80)
        for section, time_taken in section_times.items():
            logger.info(f"{section}: {time_taken:.2f} seconds")
        logger.info("=" * 80)
        logger.info(f"Total processing time for {ticker}: {int(hours):02d}:{int(minutes):02d}:{int(seconds):02d} (hh:mm:ss)")
        logger.info("=" * 80)
        logger.info("Load complete. Data is now available in the Dash app.")

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

# Update the callback decorator to match the new outputs
@app.callback(
    [Output('secondary-capture-chart', 'figure'),
     Output('secondary-metrics-table', 'data'),
     Output('secondary-metrics-table', 'columns'),
     Output('secondary-ticker-input-feedback', 'children')],
    [Input('ticker-input', 'value'),
     Input('secondary-ticker-input', 'value'),
     Input('invert-signals-toggle', 'value'),
     Input('show-secondary-annotations-toggle', 'value'),
     Input('update-interval', 'n_intervals')],
    prevent_initial_call=True
)
def update_secondary_capture_chart(primary_ticker, secondary_tickers_input, invert_signals, show_annotations, n_intervals):
    if not primary_ticker or not secondary_tickers_input:
        empty_fig = go.Figure()
        empty_fig.update_layout(template='plotly_dark')
        return empty_fig, [], [], ''

    try:
        # Parse secondary tickers
        secondary_tickers = [ticker.strip().upper() for ticker in secondary_tickers_input.split(',') if ticker.strip()]
        if not secondary_tickers:
            raise ValueError("No valid tickers found in input")
        # Remove duplicates while preserving order
        secondary_tickers = list(dict.fromkeys(secondary_tickers))
        logger.info(f"Parsed secondary tickers: {secondary_tickers}")
    except Exception as e:
        logger.error(f"Error parsing secondary tickers: {str(e)}")
        empty_fig = go.Figure()
        empty_fig.update_layout(template='plotly_dark')
        return empty_fig, [], [], 'Please enter valid comma-separated ticker symbols'

    # Check processing status of the primary ticker
    status = read_status(primary_ticker)
    if status['status'] != 'complete':
        empty_fig = go.Figure()
        empty_fig.update_layout(template='plotly_dark')
        return empty_fig, [], [], 'Primary ticker data is still processing...'

    # Load primary ticker results
    logger.info(f"Loading precomputed results for {primary_ticker.upper()}")
    results = load_precomputed_results(primary_ticker.upper())
    if results is None:
        empty_fig = go.Figure()
        empty_fig.update_layout(template='plotly_dark')
        return empty_fig, [], [], 'Primary ticker data is still loading.'

    # Validate that required data is present
    if 'active_pairs' not in results or 'cumulative_combined_captures' not in results:
        logger.error(f"Missing required data in primary ticker results. Available keys: {results.keys()}")
        return empty_fig, [], [], 'Primary ticker data is incomplete.'

    logger.info(f"Updating secondary capture chart for {secondary_tickers_input.upper()} following {primary_ticker.upper()}")
    logger.info(f"Primary ticker status: {status['status']}")

    try:
        # Fetch secondary ticker data for all tickers
        secondary_dfs = {}
        for ticker in secondary_tickers:
            df = fetch_data(ticker, is_secondary=True)
            if df is None or df.empty:
                logger.warning(f"Unable to fetch data for {ticker.upper()}. Skipping.")
                continue
            secondary_dfs[ticker] = df

        if not secondary_dfs:
            empty_fig = go.Figure()
            empty_fig.update_layout(template='plotly_dark')
            return empty_fig, [], [], 'Unable to fetch data for any secondary tickers.'

        # Get signals and dates from primary ticker
        active_pairs = results['active_pairs']
        cumulative_combined_captures = results['cumulative_combined_captures']
        dates = cumulative_combined_captures.index

        logger.info(f"Found {len(active_pairs)} active pairs and {len(dates)} dates for analysis")

        # Initialize the figure before the loop
        fig = go.Figure()

        # Initialize metrics list
        metrics_list = []

        for ticker, secondary_df in secondary_dfs.items():
            # Align dates between primary and secondary data
            common_dates = dates.intersection(secondary_df.index)
            if len(common_dates) < 2:
                logger.warning(f"Insufficient overlapping data between {primary_ticker.upper()} and {ticker.upper()}. Skipping.")
                continue

            # Create aligned series using NumPy for better performance
            signals = pd.Series(active_pairs, index=dates).loc[common_dates]
            signals_array = signals.values.astype(str)

            # Vectorized signal inversion using NumPy
            if invert_signals:
                logger.info(f"Inverting signals for secondary chart {ticker.upper()}")
                signals_array = np.where(
                    np.char.startswith(signals_array, 'Buy'),
                    np.char.replace(signals_array, 'Buy', 'Short', count=1),
                    np.where(
                        np.char.startswith(signals_array, 'Short'),
                        np.char.replace(signals_array, 'Short', 'Buy', count=1),
                        signals_array
                    )
                )

            # Fast signal filling using NumPy
            signals_filled = pd.Series(
                np.where(signals_array == 'nan', 'None', signals_array),
                index=common_dates
            )

            # Efficient computation of daily returns using NumPy
            secondary_prices_array = secondary_df['Close'].loc[common_dates].values
            daily_returns = np.diff(secondary_prices_array) / secondary_prices_array[:-1]
            daily_returns = np.insert(daily_returns, 0, 0)  # Add 0 for the first day

            # Vectorized signal application
            signals_filled_array = signals_filled.values.astype(str)
            buy_mask = np.char.startswith(signals_filled_array, 'Buy')
            short_mask = np.char.startswith(signals_filled_array, 'Short')

            daily_captures_array = np.zeros(len(signals_filled))
            daily_captures_array[buy_mask] = daily_returns[buy_mask] * 100
            daily_captures_array[short_mask] = -daily_returns[short_mask] * 100

            daily_captures = pd.Series(daily_captures_array, index=signals_filled.index)

            # Handle NaN values
            daily_captures = daily_captures.fillna(0.0)

            # Shift daily captures forward by one day to align with correct dates
            daily_captures = daily_captures.shift(1).fillna(0.0)

            # Compute cumulative captures
            cumulative_captures = daily_captures.cumsum()

            # Calculate all metrics using vectorized operations
            metrics = {
                'Ticker': ticker,
                'Trigger Days': int(np.sum(buy_mask | short_mask)),
                'Daily Captures': daily_captures[daily_captures != 0]
            }
            
            if metrics['Trigger Days'] > 0:
                metrics['Wins'] = int(np.sum(metrics['Daily Captures'] > 0))
                metrics['Losses'] = metrics['Trigger Days'] - metrics['Wins']
                metrics['Win Ratio (%)'] = round(metrics['Wins'] / metrics['Trigger Days'] * 100, 2)
                metrics['Avg Daily Capture (%)'] = round(np.mean(metrics['Daily Captures']), 4)
                metrics['Total Capture (%)'] = round(float(cumulative_captures.iloc[-1]), 4)
            else:
                metrics.update({
                    'Wins': 0,
                    'Losses': 0,
                    'Win Ratio (%)': 0.0,
                    'Avg Daily Capture (%)': 0.0,
                    'Total Capture (%)': 0.0
                })
            
            metrics_list.append({k: v for k, v in metrics.items() if k != 'Daily Captures'})
            logger.info(f"Processed {ticker} - "
                       f"Total Capture: {metrics['Total Capture (%)']}%, "
                       f"Win Ratio: {metrics['Win Ratio (%)']}%, "
                       f"Trigger Days: {metrics['Trigger Days']}")
            # Add trace to figure
            fig.add_trace(go.Scatter(
                x=cumulative_captures.index,
                y=cumulative_captures,
                mode='lines',
                name=f'{ticker}',
                line=dict(width=2),
                hovertemplate=(
                    "Ticker: " + ticker + "<br>" +
                    "Date: %{x}<br>" +
                    "Cumulative Capture: %{y:.2f}%<br>" +
                    "Signal: %{customdata}<br>" +
                    "<extra></extra>"
                ),
                customdata=signals_filled.values
            ))

        # Check if any metrics were calculated
        if not metrics_list:
            empty_fig = go.Figure()
            empty_fig.update_layout(template='plotly_dark')
            return empty_fig, [], [], 'No data available for the selected tickers.'

        # Convert metrics list to DataFrame
        metrics_df = pd.DataFrame(metrics_list)

        # Sort metrics DataFrame by Avg Daily Capture in descending order
        metrics_df.sort_values(by='Avg Daily Capture (%)', ascending=False, inplace=True)

        # Prepare DataTable columns and data
        columns = [{'name': col, 'id': col} for col in metrics_df.columns]
        data = metrics_df.to_dict('records')

        # Adjust figure layout
        fig.update_layout(
            title=dict(
                text=f'{", ".join(secondary_dfs.keys())} Performance Following {primary_ticker.upper()} {"(Inverted)" if invert_signals else ""} Signals',
                font=dict(color='#80ff00')
            ),
            xaxis_title='Date',
            yaxis_title='Cumulative Combined Capture (%)',
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

        # Only add signal changes as vertical lines and annotations if toggle is on
        if show_annotations:
            logger.info("Adding signal change annotations to secondary chart")
            shapes = []
            annotations = []
            
            # Get signal changes once since all tickers follow the same primary signal
            signals = pd.Series(active_pairs, index=dates).loc[common_dates]
            if invert_signals:
                signals = signals.apply(lambda x: 'Short' if x.startswith('Buy') else 'Buy' if x.startswith('Short') else x)
            
            signal_changes = signals != signals.shift(1)
            signal_change_dates = signals.index[signal_changes]
            signal_change_values = signals[signal_changes]
            
            # Add shapes and annotations for signal changes
            for date, signal in zip(signal_change_dates, signal_change_values):
                # Add vertical line shape
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
                
                # Add annotation with simplified text
                annotations.append(dict(
                    x=date,
                    y=1,
                    xref="x",
                    yref="paper",
                    text=signal,  # Just show the signal type without ticker name
                    showarrow=False,
                    font=dict(
                        color="#80ff00",
                        size=10
                    ),
                    bgcolor="rgba(0,0,0,0.5)",
                    xanchor='left',
                    yanchor='top'
                ))
            
            # Update layout with all shapes and annotations
            fig.update_layout(
                shapes=shapes,
                annotations=annotations
            )
        else:
            # Ensure shapes and annotations are empty when toggle is off
            fig.update_layout(
                shapes=[],
                annotations=[]
            )

        return fig, data, columns, ''

    except Exception as e:
        logger.error(f"Error in secondary capture chart: {str(e)}")
        logger.error(traceback.format_exc())
        empty_fig = go.Figure()
        empty_fig.update_layout(template='plotly_dark')
        return empty_fig, [], [], f'Error: {str(e)}'

if __name__ == "__main__":
    # Run the Dash app
    app.run_server(debug=True)