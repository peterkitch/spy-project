import yfinance as yf
import plotly.graph_objects as go
from dash import Dash
import dash.dcc as dcc
import dash.html as html
from dash.dependencies import Input, Output, State
import dash_bootstrap_components as dbc
import pandas as pd
from functools import lru_cache
from functools import partial
import pickle
from tqdm import tqdm
import os
import json
import tempfile
import shutil
import time
import numpy as np
import gc
import copy
import threading
from threading import Lock
import sys
from pprint import pformat
from joblib import Memory
import logging
from tqdm.contrib.logging import logging_redirect_tqdm
import io
import multiprocessing
import bz2
import lzma
import zlib
import gzip
import traceback
import random
import glob

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
master_stopwatch_start = None
MAX_SMA_DAY = 113

_precomputed_results_cache = {}
_loading_in_progress = {}
_loading_lock = threading.Lock()

status_lock = Lock()

# Set up persistent cache
cache_dir = '.cache'
os.makedirs(cache_dir, exist_ok=True)
memory = Memory(cache_dir, verbose=0)

@lru_cache(maxsize=5)
def fetch_data(ticker):
    try:
        df = yf.download(ticker, period='max', interval='1d', progress=False)
        df.index = pd.to_datetime(df.index)
        logging.info(f"Successfully fetched data for {ticker}")
        return df
    except Exception as e:
        logging.error(f"Failed to fetch data for {ticker}: {type(e).__name__} - {str(e)}")
        return pd.DataFrame()

def identify_new_trading_days(df, existing_data):
    if existing_data is not None and not existing_data.empty:
        latest_existing_date = existing_data.index.max()
        new_trading_days = df.loc[latest_existing_date:].iloc[1:]
        if not new_trading_days.empty:
            if len(new_trading_days) >= 1:
                print(f"New trading day found: {new_trading_days.index[0]}")
            else:
                print(f"New trading days found: {new_trading_days.index.min()} to {new_trading_days.index.max()}")
            return new_trading_days
    return None

def get_last_modified_time(file_path):
    try:
        return os.path.getmtime(file_path)
    except OSError:
        return None

def load_precomputed_results_from_file(pkl_file, max_retries=5, delay=1):
    retries = 0
    while retries < max_retries:
        try:
            with open(pkl_file, 'rb') as f:
                results = pickle.load(f)
            return results
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
    
def load_precomputed_results(ticker):
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
            results = load_precomputed_results_from_file(pkl_file)
            if results:
                # Load buy and short results incrementally
                buy_results = {}
                short_results = {}
                chunk_files = sorted(glob.glob(f'{ticker}_results_chunk_*.pkl.gz'))
                
                with tqdm(total=len(chunk_files), desc=f"Loading chunks for {ticker}", unit="chunk") as pbar:
                    for chunk_file in chunk_files:
                        with gzip.open(chunk_file, 'rb') as f:
                            while True:
                                try:
                                    data_type, pair, value = pickle.load(f)
                                    if data_type == 'buy':
                                        buy_results[pair] = value
                                    else:
                                        short_results[pair] = value
                                except EOFError:
                                    break
                        pbar.update(1)
                
                results['buy_results'] = buy_results
                results['short_results'] = short_results
                
                # Ensure daily_top_buy_pairs and daily_top_short_pairs are loaded
                if 'daily_top_buy_pairs' not in results or 'daily_top_short_pairs' not in results:
                    logger.warning(f"Missing daily top pairs for {ticker}. Recomputing...")
                    daily_top_buy_pairs, daily_top_short_pairs = calculate_daily_top_pairs(results['preprocessed_data'], buy_results, short_results)
                    results['daily_top_buy_pairs'] = daily_top_buy_pairs
                    results['daily_top_short_pairs'] = daily_top_short_pairs
                    save_precomputed_results(ticker, results)
                
                _precomputed_results_cache[ticker] = results
                logger.debug(f"Loaded results from file for {ticker}")
                return results
            else:
                logger.warning(f"Failed to load results from file for {ticker}")

        logger.info(f"Starting to load precomputed results for {ticker}...")
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
    status_path = f"{ticker}_status.json"
    with status_lock:
        with open(status_path, 'w') as f:
            json.dump(status, f)

def save_precomputed_results(ticker, results):
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
            daily_top_buy_pairs, daily_top_short_pairs = calculate_daily_top_pairs(results['preprocessed_data'], results.get('buy_results', {}), results.get('short_results', {}))
            main_results['daily_top_buy_pairs'] = daily_top_buy_pairs
            main_results['daily_top_short_pairs'] = daily_top_short_pairs
        
        with open(temp_file_path, 'wb') as f:
            pickle.dump(main_results, f)

        # Atomically move the temp file to the final destination
        shutil.move(temp_file_path, final_file_path)
        logging.info(f"Main results saved successfully to {final_file_path}")
        logging.info(f"Number of daily_top_buy_pairs saved: {len(main_results.get('daily_top_buy_pairs', {}))}")
        logging.info(f"Number of daily_top_short_pairs saved: {len(main_results.get('daily_top_short_pairs', {}))}")

        # Save buy and short results in chunks
        chunk_size = 50000  # Adjust based on your needs
        buy_results = results.get('buy_results', {})
        short_results = results.get('short_results', {})
        
        for i in range(0, len(buy_results), chunk_size):
            chunk_buy = dict(list(buy_results.items())[i:i+chunk_size])
            chunk_short = dict(list(short_results.items())[i:i+chunk_size])
            save_precomputed_results_chunk(ticker, chunk_buy, chunk_short, i//chunk_size)

        logging.info(f"All results saved successfully for {ticker}")
    except PermissionError:
        logging.error(f"Permission denied when saving results to {final_file_path}. Please check file permissions.")
    except Exception as e:
        logging.error(f"Error saving results for {ticker}: {str(e)}")
        logging.error(traceback.format_exc())

    # Return the main_results even if an exception occurred
    return main_results

def calculate_daily_top_pairs(df, buy_results, short_results):
    log_section("Daily Top Pairs Calculation")
    logger.info("Calculating daily top pairs...")
    daily_top_buy_pairs = {}
    daily_top_short_pairs = {}

    if not buy_results or not short_results:
        logger.warning("Empty buy_results or short_results. Unable to calculate daily top pairs.")
        return {date: ((1, 2), 0.0) for date in df.index}, {date: ((1, 2), 0.0) for date in df.index}

    total_days = len(df.index)
    chunk_size = 1000  # Process 1000 days at a time

    with tqdm(total=total_days, desc="Processing daily top pairs", unit="day") as pbar:
        for chunk_start in range(0, total_days, chunk_size):
            chunk_end = min(chunk_start + chunk_size, total_days)
            chunk_dates = df.index[chunk_start:chunk_end]

            buy_chunk = {pair: result[chunk_start:chunk_end] for pair, result in buy_results.items()}
            short_chunk = {pair: result[chunk_start:chunk_end] for pair, result in short_results.items()}

            buy_array = np.array(list(buy_chunk.values()))
            short_array = np.array(list(short_chunk.values()))

            buy_max_indices = np.argmax(buy_array, axis=0)
            short_max_indices = np.argmax(short_array, axis=0)

            buy_pairs = list(buy_chunk.keys())
            short_pairs = list(short_chunk.keys())

            for i, date in enumerate(chunk_dates):
                daily_top_buy_pairs[date] = (buy_pairs[buy_max_indices[i]], float(buy_array[buy_max_indices[i], i]))
                daily_top_short_pairs[date] = (short_pairs[short_max_indices[i]], float(short_array[short_max_indices[i], i]))

                if (chunk_start + i + 1) % 1000 == 0:
                    logger.info(f"Processed {chunk_start + i + 1}/{total_days} days. "
                                f"Current buy pair: {daily_top_buy_pairs[date]}, "
                                f"Current short pair: {daily_top_short_pairs[date]}")

            pbar.update(len(chunk_dates))

    logger.info(f"Number of daily top pairs: Buy: {len(daily_top_buy_pairs)}, Short: {len(daily_top_short_pairs)}")
    if daily_top_buy_pairs:
        logger.info(f"Sample buy pair: {next(iter(daily_top_buy_pairs.items()))}")
    if daily_top_short_pairs:
        logger.info(f"Sample short pair: {next(iter(daily_top_short_pairs.items()))}")

    # Log the first few and last few pairs
    first_few = list(daily_top_buy_pairs.items())[:5]
    last_few = list(daily_top_buy_pairs.items())[-5:]
    logger.info(f"First few buy pairs: {first_few}")
    logger.info(f"Last few buy pairs: {last_few}")
    
    # Log unique pairs
    unique_buy_pairs = set(pair for pair, _ in daily_top_buy_pairs.values())
    unique_short_pairs = set(pair for pair, _ in daily_top_short_pairs.values())
    logger.info(f"Unique pairs: Buy: {len(unique_buy_pairs)}, Short: {len(unique_short_pairs)}")
    logger.info("Daily top pairs calculation completed.")
    
    return daily_top_buy_pairs, daily_top_short_pairs

def calculate_captures_vectorized(sma1, sma2, returns):
    buy_signals = (sma1 > sma2) & ~np.isnan(sma1) & ~np.isnan(sma2)
    short_signals = (sma1 < sma2) & ~np.isnan(sma1) & ~np.isnan(sma2)

    # Process in chunks to reduce memory usage
    chunk_size = 1000
    num_pairs, num_days = sma1.shape
    buy_capture = np.zeros_like(sma1)
    short_capture = np.zeros_like(sma1)

    for i in range(0, num_pairs, chunk_size):
        end = min(i + chunk_size, num_pairs)
        buy_chunk = np.nancumsum(np.where(np.roll(buy_signals[i:end], 1, axis=1), returns, 0), axis=1)
        short_chunk = np.nancumsum(np.where(np.roll(short_signals[i:end], 1, axis=1), -returns, 0), axis=1)
        buy_capture[i:end] = buy_chunk
        short_capture[i:end] = short_chunk

    # Add logging to check the calculated captures
    logging.info(f"Sample buy capture: {buy_capture[0, :5]}")
    logging.info(f"Sample short capture: {short_capture[0, :5]}")
    
    return buy_capture, short_capture

def save_precomputed_results_chunk(ticker, buy_results, short_results, chunk_index):
    chunk_file = f'{ticker}_results_chunk_{chunk_index}.pkl.gz'
    try:
        total_pairs = len(buy_results) + len(short_results)
        with tqdm(total=total_pairs, desc=f"Saving chunk {chunk_index}", unit="pair", dynamic_ncols=True, mininterval=0.1) as pbar:
            with gzip.open(chunk_file, 'wb', compresslevel=1) as f:
                for i, (pair, value) in enumerate(buy_results.items()):
                    pickle.dump(('buy', pair, value), f, protocol=pickle.HIGHEST_PROTOCOL)
                    if i % 1000 == 0:
                        pbar.update(1000)
                for i, (pair, value) in enumerate(short_results.items()):
                    pickle.dump(('short', pair, value), f, protocol=pickle.HIGHEST_PROTOCOL)
                    if i % 1000 == 0:
                        pbar.update(1000)
                pbar.update(total_pairs % 1000)
        
        file_size = os.path.getsize(chunk_file) / (1024 * 1024 * 1024)  # Size in GB
        logging.info(f"Results saved successfully to {chunk_file} (Size: {file_size:.2f} GB)")
    except Exception as e:
        logging.error(f"Error saving results for {ticker}: {str(e)}")

def precompute_results(ticker, event):
    global _loading_in_progress, _precomputed_results_cache
    with logging_redirect_tqdm():
        try:
            logger.info(f"precompute_results called for {ticker}")
            
            df = fetch_data(ticker)
            if df is None or df.empty:
                write_status(ticker, {"status": "failed", "message": "No data"})
                logger.warning(f"No data fetched for {ticker}")
                return None
            
            log_section("Data Preprocessing")
            logger.info(f"Data fetched and preprocessed for {ticker}")

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

            log_section("SMA Calculation")
            logger.info("Calculating new SMA columns...")
            chunk_size = 1000  # Increased from 50
            for i in range(existing_max_sma_day + 1, max_sma_day + 1, chunk_size):
                chunk_end = min(i + chunk_size, max_sma_day + 1)
                sma_columns = {}
                for j in range(i, chunk_end):
                    sma_columns[f'SMA_{j}'] = df['Close'].rolling(window=j, min_periods=j).mean()
                sma_df = pd.DataFrame(sma_columns)
                df = pd.concat([df, sma_df], axis=1)
                del sma_df
                gc.collect()
            logger.info(f"Added {max_sma_day - existing_max_sma_day} new SMA columns to DataFrame.")

            # Ensure NaN values for the first j-1 rows of each SMA column
            for j in range(1, max_sma_day + 1):
                df.iloc[:j-1, df.columns.get_loc(f'SMA_{j}')] = np.nan

            logger.info("Ensured correct NaN values for SMA calculations.")
            
            # Pre-calculate all SMAs
            sma_columns = {i: df['Close'].rolling(window=i).mean().values for i in range(1, max_sma_day + 1)}

            new_sma_pairs = [
                (i, j) for i in range(existing_max_sma_day + 1, max_sma_day)
                for j in range(i + 1, max_sma_day + 1)
            ]
            total_pairs = len(new_sma_pairs)
            returns = df['Close'].pct_change().values

            log_section("Capture Calculation")
            chunk_size = 100000  # Increased chunk size for better performance
            update_interval = 1000  # Update progress more frequently
            buy_results = {}
            short_results = {}
            with tqdm(total=total_pairs, desc=f'Calculation for {ticker}', unit='pair', dynamic_ncols=True, mininterval=0.1) as pbar:
                for i in range(0, total_pairs, chunk_size):
                    chunk_pairs = new_sma_pairs[i:i+chunk_size]
                    
                    sma1_array = np.array([sma_columns[pair[0]] for pair in chunk_pairs])
                    sma2_array = np.array([sma_columns[pair[1]] for pair in chunk_pairs])
                    
                    buy_captures, short_captures = calculate_captures_vectorized(sma1_array, sma2_array, returns)
                    
                    for j, pair in enumerate(chunk_pairs):
                        buy_results[pair] = buy_captures[j]
                        short_results[pair] = short_captures[j]
                        inverse_pair = (pair[1], pair[0])
                        buy_results[inverse_pair] = -short_captures[j]
                        short_results[inverse_pair] = -buy_captures[j]
                        
                        if (i + j + 1) % update_interval == 0:
                            pbar.update(update_interval)
                            # Update progress
                            write_status(ticker, {"status": "processing", "progress": (i + j + 1) / total_pairs * 100})
                    
                # Save results after processing each chunk
                save_precomputed_results_chunk(ticker, buy_results, short_results, i // chunk_size)
                
                # Update final progress
                write_status(ticker, {"status": "processing", "progress": 100})
                
                # Update any remaining progress
                remaining = total_pairs % update_interval
                if remaining > 0:
                    pbar.update(remaining)
                    write_status(ticker, {"status": "processing", "progress": 100})

            logger.info(f"Processed {total_pairs} SMA pairs for {ticker}")
            logger.info(f"Total buy pairs: {total_pairs * 2}, Total short pairs: {total_pairs * 2}")
            
            # Update other results
            results['preprocessed_data'] = df
            results['existing_max_sma_day'] = max_sma_day
            results['last_processed_date'] = df.index[-1]
            results['start_date'] = start_date
            results['last_date'] = last_date
            results['total_trading_days'] = total_trading_days

            log_section("Daily Top Pairs Calculation")
            daily_top_buy_pairs, daily_top_short_pairs = calculate_daily_top_pairs(df, buy_results, short_results)

            results['daily_top_buy_pairs'] = daily_top_buy_pairs
            results['daily_top_short_pairs'] = daily_top_short_pairs

            # Save the results after calculating daily top pairs
            save_precomputed_results(ticker, results)
        
            log_section("Cumulative Combined Captures")
            cumulative_combined_captures, active_pairs = calculate_cumulative_combined_capture(df, daily_top_buy_pairs, daily_top_short_pairs)
            
            results['cumulative_combined_captures'] = cumulative_combined_captures
            results['active_pairs'] = active_pairs
        
            # Identify top performing pairs
            top_buy_pair = max(daily_top_buy_pairs.items(), key=lambda x: x[1][1])
            top_short_pair = max(daily_top_short_pairs.items(), key=lambda x: x[1][1])

            logger.info(f"Current Top buy pair: {top_buy_pair[1][0]} with results {top_buy_pair[1][1]:.6f}")
            logger.info(f"Current Top short pair: {top_short_pair[1][0]} with results {top_short_pair[1][1]:.6f}")

            results['top_buy_pair'] = top_buy_pair[1][0]
            results['top_short_pair'] = top_short_pair[1][0]
            
            # Save final results
            logger.info(f"Saving final results to {pkl_file}")
            save_precomputed_results(ticker, results)
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
            app.layout = app.layout

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

# Initialize the Dash app with a dark theme and custom styles
app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])

# Function to read the processing status from a file
def read_status(ticker):
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
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader('Select Ticker Symbol'),
                    dbc.CardBody([
                        dbc.Input(id='ticker-input', placeholder='Enter a valid ticker symbol (e.g., AAPL)', type='text', debounce=True),
                        dbc.FormFeedback(id='ticker-input-feedback', className='text-danger')
                    ])
                ], className='mb-3')
            ], width=12)
        ]),
        dbc.Row([
            dbc.Col([
                dcc.Loading(
                    id="loading-combined-capture",
                    type="default",
                    children=[dcc.Graph(id='combined-capture-chart')]
                )
            ], width=12)
        ]),
        dbc.Row([
            dbc.Col([
                dcc.Loading(
                    id="loading-historical-top-pairs",
                    type="default",
                    children=[dcc.Graph(id='historical-top-pairs-chart')]
                )
            ], width=12)
        ]),
        dbc.Row([
            dbc.Col([
                dbc.Switch(
                    id='show-annotations-toggle',
                    label='Show Signal Annotations',
                    value=False  # Default to hiding annotations
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
                        html.H5('Calculation Components', className='mb-0'),
                        html.Button('Minimize', id='toggle-calc-button', className='btn btn-sm btn-secondary ml-auto')
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
                        html.Button('Minimize', id='toggle-strategy-button', className='btn btn-sm btn-secondary ml-auto')
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
                        is_open=True
                    )
                ])
            ], width=12)
        ]),
        dcc.Interval(id='update-interval', interval=5000, n_intervals=0, disabled=False),  # Decreased to 5 second from 30 seconds
        dcc.Interval(id='loading-interval', interval=2000, n_intervals=0),  # Update every 2 seconds
        dcc.Loading(
            id="loading-spinner",
            type="default",
            children=[html.Div(id="loading-spinner-output")]
        ),
    ]
)

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
    Output('calc-collapse', 'is_open'),
    [Input('toggle-calc-button', 'n_clicks')],
    [State('calc-collapse', 'is_open')],
)
def toggle_calc_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open
    return is_open

# Callback to toggle the visibility of the Dynamic Master Trading Strategy section
@app.callback(
    Output('strategy-collapse', 'is_open'),
    [Input('toggle-strategy-button', 'n_clicks')],
    [State('strategy-collapse', 'is_open')],
)
def toggle_strategy_collapse(n_clicks, is_open):
    if n_clicks:
        return not is_open
    return is_open

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

def calculate_trading_signals(df, daily_top_buy_pairs, daily_top_short_pairs, buy_results, short_results):
    trading_signals = {}
    current_position = None

    for date in df.index:
        top_buy_pair = daily_top_buy_pairs.get(date)
        top_short_pair = daily_top_short_pairs.get(date)

        buy_signal = df[f'SMA_{top_buy_pair[0]}'].loc[date] > df[f'SMA_{top_buy_pair[1]}'].loc[date] if top_buy_pair else False
        short_signal = df[f'SMA_{top_short_pair[0]}'].loc[date] < df[f'SMA_{top_short_pair[1]}'].loc[date] if top_short_pair else False

        if buy_signal and short_signal:
            if current_position == 'short':
                trading_signal = 'buy'
            elif current_position == 'buy':
                trading_signal = 'short'
            else:
                trading_signal = 'buy' if buy_results[top_buy_pair].loc[date] > short_results[top_short_pair].loc[date] else 'short'
        elif buy_signal:
            trading_signal = 'buy'
        elif short_signal:
            trading_signal = 'short'
        else:
            trading_signal = 'cash'

        trading_signals[date] = (top_buy_pair, top_short_pair, trading_signal)
        current_position = trading_signal

    return trading_signals

def calculate_cumulative_combined_capture(df, daily_top_buy_pairs, daily_top_short_pairs):
    log_separator()
    logger.info("Calculating cumulative combined capture")
    logger.info(f"Number of trading days: {len(df)}")

    if not daily_top_buy_pairs or not daily_top_short_pairs:
        tqdm.write("No daily top pairs available for processing cumulative combined captures.")
        return pd.Series([0], index=[df.index[0]]), ['None']

    dates = sorted(set(daily_top_buy_pairs.keys()) | set(daily_top_short_pairs.keys()))
    cumulative_combined_captures = []  # Start with empty lists
    active_pairs = []
    cumulative_capture = 0

    tqdm.write("\nCalculating cumulative combined capture...")
    with tqdm(total=len(dates), unit='date', desc='Processing dates', 
              bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]', 
              ncols=100, mininterval=0.5, smoothing=0.01, leave=True) as pbar:
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

            # Update the progress bar for each iteration
            pbar.update(1)

        # Update postfix at the very end
        pbar.set_postfix({'Cumulative Capture': f'{cumulative_capture:.2f}%'})

    # After the loop, print a summary
    logger.info("Cumulative Capture Summary:")
    logger.info(f"Date range: {dates[0]} to {dates[-1]}")
    logger.info(f"Total Trading Days: {len(dates)}")
    logger.info(f"Final Cumulative Capture: {cumulative_capture:.2f}%")
    logger.info(f"Final Active Pair: {active_pairs[-1]}")
    log_separator()

    return pd.Series(cumulative_combined_captures, index=dates), active_pairs

def get_or_calculate_combined_captures(results, df, daily_top_buy_pairs, daily_top_short_pairs, ticker):
    if 'cumulative_combined_captures' in results and 'active_pairs' in results:
        cumulative_combined_captures = results['cumulative_combined_captures']
        active_pairs = results['active_pairs']
        print("Using stored cumulative_combined_captures and active_pairs")
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
    
    print(f"Loaded preprocessed_data with {len(df)} trading days.")
    
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
        return go.Figure(layout=go.Layout(title="Please enter a ticker symbol."))
    
    results = load_precomputed_results(ticker)
    if results is None:
        return go.Figure(layout=go.Layout(title=f"Loading data for {ticker}..."))

    results, df, daily_top_buy_pairs, daily_top_short_pairs, cumulative_combined_captures, active_pairs = load_and_prepare_data(ticker)
    if results is None or df is None or daily_top_buy_pairs is None or daily_top_short_pairs is None or cumulative_combined_captures is None or active_pairs is None:
        return go.Figure(layout=go.Layout(title=f"No data available for {ticker}"))

    if len(cumulative_combined_captures) == 1 and active_pairs == ['None']:
        return go.Figure(layout=go.Layout(title=f"No capture data available for {ticker}"))

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

    print(f"Sample data rows:\n{data.head()}\n{data.tail()}")

    # Calculate the next day's active pair for the last day
    last_date = data['date'].iloc[-1]
    top_buy_pair = daily_top_buy_pairs.get(last_date, ((0, 0), 0))[0]
    top_short_pair = daily_top_short_pairs.get(last_date, ((0, 0), 0))[0]
    
    if top_buy_pair[0] != 0 and top_buy_pair[1] != 0:
        sma_long = df[f'SMA_{top_buy_pair[0]}'].iloc[-1]
        sma_short = df[f'SMA_{top_buy_pair[1]}'].iloc[-1]
        if sma_long > sma_short:
            data.loc[data.index[-1], 'active_pair_next'] = f"Buy ({top_buy_pair[0]},{top_buy_pair[1]})"
        elif sma_long < sma_short:
            data.loc[data.index[-1], 'active_pair_next'] = f"Short ({top_short_pair[0]},{top_short_pair[1]})"
        else:
            data.loc[data.index[-1], 'active_pair_next'] = "None"
    else:
        data.loc[data.index[-1], 'active_pair_next'] = "None"

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
        line=dict(color='blue'),
    ))

    fig.update_layout(
        title=f'{ticker} Cumulative Combined Capture',
        xaxis_title='Trading Day',
        yaxis_title='Cumulative Combined Capture (%)',
        hovermode='x',
        template='plotly_dark',
    )

    return fig

@app.callback(
    Output('historical-top-pairs-chart', 'figure'),
    [Input('ticker-input', 'value'),
     Input('show-annotations-toggle', 'value'),
     Input('update-interval', 'n_intervals')]
)
def update_historical_top_pairs_chart(ticker, show_annotations, n_intervals):
    if not ticker:
        return go.Figure(layout=go.Layout(title="Please enter a ticker symbol."))

    logger.debug(f"Updating historical top pairs chart for {ticker}")

    try:
        results = load_precomputed_results(ticker)
        if results is not None:
            logger.debug(f"Results keys: {list(results.keys())}")
        else:
            logger.debug("Results is None.")
            return go.Figure(layout=go.Layout(title=f"Loading data for {ticker}..."))

        # Check if required data is present
        required_keys = ['preprocessed_data', 'daily_top_buy_pairs', 'daily_top_short_pairs', 'cumulative_combined_captures', 'active_pairs']
        missing_keys = [key for key in required_keys if key not in results]
        
        if missing_keys:
            logger.error(f"Missing required data: {missing_keys}")
            return go.Figure(layout=go.Layout(title=f"Error: Missing data for {ticker}. Please recompute results."))

        # Extract required data from results
        df = results['preprocessed_data']
        daily_top_buy_pairs = results['daily_top_buy_pairs']
        daily_top_short_pairs = results['daily_top_short_pairs']
        cumulative_combined_captures = results['cumulative_combined_captures']
        active_pairs = results['active_pairs']

        log_separator()
        logger.info(f"Number of trading days: {len(df)}")
        logger.info(f"Number of daily top buy pairs: {len(daily_top_buy_pairs)}")
        logger.info(f"Number of daily top short pairs: {len(daily_top_short_pairs)}")
        log_separator()

        fig = go.Figure()

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

        log_separator()
        logger.info(f"Number of colors: {len(colors)}")
        logger.info(f"Unique colors: {set(colors)}")
        log_separator()

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

        log_separator()
        logger.info(f"Number of color segments: {len(color_segments)}")
        log_separator()

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
        annotation_count = 0
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
                annotation_count += 1

            last_pair = pair

        # Only add annotations if the toggle is on
        if show_annotations:
            fig.update_layout(annotations=annotations)
        else:
            fig.update_layout(annotations=[])

        log_separator()
        logger.info(f"Number of annotations created: {annotation_count}")
        logger.info(f"Annotations display is set to: {'On' if show_annotations else 'Off'}")
        log_separator()

        fig.update_layout(
            title=f'{ticker} Historical Top Pairs Performance',
            xaxis_title='Trading Day',
            yaxis_title='Cumulative Capture (%)',
            hovermode='x unified',
            template='plotly_dark',
            showlegend=False
        )
        log_separator()
        logger.info("Historical Chart Update COMPLETE")
        log_separator()
        return fig

    except Exception as e:
        print(f"Error in update_historical_top_pairs_chart: {str(e)}")
        import traceback
        traceback.print_exc()
        return go.Figure(layout=go.Layout(title=f"Error generating chart for {ticker}: {str(e)}"))

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
    buy_results = results.get('buy_results', {})
    short_results = results.get('short_results', {})

    # Ensure that 'preprocessed_data' exists and is correctly formatted
    df = results.get('preprocessed_data')
    if df is None or df.empty:
        print(f"Warning: 'preprocessed_data' is missing or empty for {ticker}")
        return ["Data integrity issue. Please check the precomputed results."] * 10

    if top_buy_pair is None or top_short_pair is None or df is None:
        return ["Data integrity issue. Please check the precomputed results."] * 10

    try:
        sma1_buy_leader = df[f'SMA_{top_buy_pair[0]}']
        sma2_buy_leader = df[f'SMA_{top_buy_pair[1]}']
        buy_signals_leader = sma1_buy_leader > sma2_buy_leader
        close_pct_change = df['Close'].pct_change().values
        buy_returns_leader = np.where(np.roll(buy_signals_leader, 1), close_pct_change, 0)

        sma1_short_leader = df[f'SMA_{top_short_pair[0]}']
        sma2_short_leader = df[f'SMA_{top_short_pair[1]}']
        short_signals_leader = sma1_short_leader < sma2_short_leader
        short_returns_leader = np.where(np.roll(short_signals_leader, 1), -close_pct_change, 0)

    except KeyError:
        print(f"Required SMA columns not found in the DataFrame for {ticker}")
        return ["Data not available or processing not yet complete. Please wait..."] * 10

    buy_signal = sma1_buy_leader.iloc[-1] > sma2_buy_leader.iloc[-1]
    short_signal = sma1_short_leader.iloc[-1] < sma2_short_leader.iloc[-1]

    if buy_signal and not short_signal:
        trading_signal = "Current Trading Signal: Buy"
        active_leader_returns = buy_returns_leader
    elif short_signal and not buy_signal:
        trading_signal = "Current Trading Signal: Short"
        active_leader_returns = short_returns_leader
    else:
        trading_signal = "Current Trading Signal: Cash (No active triggers)"
        active_leader_returns = np.zeros_like(buy_returns_leader)

    most_productive_buy_pair_text = f"Most Productive Buy Pair: SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]}"
    most_productive_short_pair_text = f"Most Productive Short Pair: SMA {top_short_pair[0]} / SMA {top_short_pair[1]}"

    buy_trigger_days = (buy_returns_leader != 0).sum()
    short_trigger_days = (short_returns_leader != 0).sum()

    avg_capture_buy = np.mean(buy_returns_leader[buy_returns_leader != 0]) if buy_trigger_days > 0 else 0
    avg_capture_short = np.mean(short_returns_leader[short_returns_leader != 0]) if short_trigger_days > 0 else 0

    avg_capture_buy_leader = f"Avg. Daily Capture % for Buy Leader: {avg_capture_buy * 100:.4f}% (Trigger Days: {buy_trigger_days})"
    avg_capture_short_leader = f"Avg. Daily Capture % for Short Leader: {avg_capture_short * 100:.4f}% (Trigger Days: {short_trigger_days})"

    def get_capture(results, pair):
        if pair in results:
            value = results[pair]
            return value[-1] if isinstance(value, np.ndarray) else value.iloc[-1] if isinstance(value, pd.Series) else value
        return 0

    buy_capture = get_capture(buy_results, top_buy_pair) or -get_capture(short_results, (top_buy_pair[1], top_buy_pair[0]))
    total_capture_buy_leader = f"Total Capture for Buy Leader: {buy_capture * 100:.4f}%"

    short_capture = get_capture(short_results, top_short_pair) or -get_capture(buy_results, (top_short_pair[1], top_short_pair[0]))
    total_capture_short_leader = f"Total Capture for Short Leader: {short_capture * 100:.4f}%"

    # Retrieve precomputed cumulative combined capture data
    cumulative_combined_captures = results.get('cumulative_combined_captures', pd.Series())
    print(f"\ncumulative_combined_captures for {ticker}:\n{cumulative_combined_captures}")

    if not cumulative_combined_captures.empty:
        combined_total_capture = cumulative_combined_captures.iloc[-1]
        combined_returns = cumulative_combined_captures.pct_change().fillna(0)
        
        combined_trigger_days = (combined_returns != 0).sum()
        combined_wins = (combined_returns > 0).sum()
        combined_losses = (combined_returns < 0).sum()
        combined_win_ratio = combined_wins / combined_trigger_days if combined_trigger_days > 0 else 0
        combined_avg_capture = combined_returns[combined_returns != 0].mean() if combined_trigger_days > 0 else 0

        combined_strategy_text = f"""
        Combined Strategy Performance:
        Total Capture: {combined_total_capture:.4f}%
        Avg. Daily Capture: {combined_avg_capture * 100:.4f}%
        Win Ratio: {combined_win_ratio * 100:.2f}%
        Trigger Days: {combined_trigger_days}
        Wins: {combined_wins}, Losses: {combined_losses}
        """
    else:
        combined_strategy_text = "No combined strategy data available."

    active_trigger_days = (active_leader_returns != 0).sum()
    if active_trigger_days > 0:
        performance_expectation = np.mean(active_leader_returns[active_leader_returns != 0])
        confidence_percentage = np.sum(active_leader_returns > 0) / active_trigger_days
        performance_expectation_text = f"Performance Expectation: {performance_expectation * 100:.4f}% (Trigger Days: {active_trigger_days})"
        confidence_percentage_text = f"Win Ratio: {confidence_percentage * 100:.2f}% (Wins: {np.sum(active_leader_returns > 0)}, Losses: {np.sum(active_leader_returns < 0)})"
    else:
        performance_expectation_text = "Performance Expectation: N/A (No active triggers)"
        confidence_percentage_text = "Win Ratio: N/A (No active triggers)"

    sma_buy_slow = df[f'SMA_{top_buy_pair[0]}'].iloc[-1]
    sma_short_slow = df[f'SMA_{top_short_pair[0]}'].iloc[-1]

    buy_threshold = sma_buy_slow * 1.005
    short_threshold = sma_short_slow * 0.995

    trading_recommendations = [
        html.H6('Trading Recommendations for Next Day', style={'margin-top': '20px', 'margin-bottom': '10px'}),
        html.Div([
            html.P(f"Leading Buy SMA Pair: SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]}"),
            html.P(f"Buy Signal Active: {'Yes' if buy_signal else 'No'}"),
            html.P(f"Buy if Close is below: {buy_threshold:.2f}") if buy_signal else html.P("No Buy Signal"),
            html.P(f"Leading Short SMA Pair: SMA {top_short_pair[0]} / SMA {top_short_pair[1]}"),
            html.P(f"Short Signal Active: {'Yes' if short_signal else 'No'}"),
            html.P(f"Short if Close is above: {short_threshold:.2f}") if short_signal else html.P("No Short Signal"),
            html.P("Recommendation: Cash Position") if not buy_signal and not short_signal else None
        ])
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
        html.Div([
            html.P(combined_strategy_text),
            html.Div(trading_recommendations)
        ])
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
    if ticker is None or any(sma_day is None for sma_day in [sma_day_1, sma_day_2, sma_day_3, sma_day_4]):
        return go.Figure(), '', '', '', '', '', '', '', ''

    df = fetch_data(ticker)
    if df is None or df.empty:
        return go.Figure(layout=go.Layout(title=f"No data available for {ticker}")), '', '', '', '', '', '', '', ''

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
    buy_returns = daily_returns.where(buy_signals.shift(1, fill_value=False), 0)
    short_returns = -daily_returns.where(short_signals.shift(1, fill_value=False), 0)

    # Calculate cumulative capture for Buy and Short
    total_buy_capture = buy_returns.cumsum()
    total_short_capture = short_returns.cumsum()

    # Create the chart figure
    fig = go.Figure()

    # Add closing prices trace
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name=f'{ticker} Close'))

    # Add SMA traces
    fig.add_trace(go.Scatter(x=df.index, y=sma1_buy, mode='lines', name=f'SMA {sma_day_1} (Buy)', visible=True))
    fig.add_trace(go.Scatter(x=df.index, y=sma2_buy, mode='lines', name=f'SMA {sma_day_2} (Buy)', visible=True))
    fig.add_trace(go.Scatter(x=df.index, y=sma1_short, mode='lines', name=f'SMA {sma_day_3} (Short)', visible=True))
    fig.add_trace(go.Scatter(x=df.index, y=sma2_short, mode='lines', name=f'SMA {sma_day_4} (Short)', visible=True))

    # Add Total Buy Capture and Total Short Capture traces
    fig.add_trace(go.Scatter(x=total_buy_capture.index, y=total_buy_capture * 100, mode='lines', name='Total Buy Capture'))
    fig.add_trace(go.Scatter(x=total_short_capture.index, y=total_short_capture * 100, mode='lines', name='Total Short Capture'))

    # Customize layout
    fig.update_layout(
        title=f'{ticker} Closing Prices, SMAs, and Total Capture (Start Date: {start_date}, Last Date: {last_date})',
        xaxis_title='Trading Day',
        yaxis_title=f'{ticker} Closing Price',
        hovermode='x',
        uirevision='static',
        template='plotly_dark'
    )

    # Calculate trigger days for Buy and Short
    trigger_days_buy = f"Buy Trigger Days: {buy_signals.sum()}"
    trigger_days_short = f"Short Trigger Days: {short_signals.sum()}"

    # Calculate detailed statistics for Buy
    buy_trigger_days = (buy_returns != 0).sum()
    buy_wins = (buy_returns > 0).sum()
    buy_losses = (buy_returns < 0).sum()
    buy_win_ratio = buy_wins / buy_trigger_days if buy_trigger_days > 0 else 0

    # Calculate detailed statistics for Short
    short_trigger_days = (short_returns != 0).sum()
    short_wins = (short_returns > 0).sum()
    short_losses = (short_returns < 0).sum()
    short_win_ratio = short_wins / short_trigger_days if short_trigger_days > 0 else 0

    # Prepare detailed strings for display
    win_ratio_buy = (f"Buy Win Ratio: {buy_win_ratio * 100:.2f}% "
                     f"(Wins: {buy_wins}, Losses: {buy_losses}, "
                     f"Trigger Days: {buy_trigger_days})")
    
    win_ratio_short = (f"Short Win Ratio: {short_win_ratio * 100:.2f}% "
                       f"(Wins: {short_wins}, Losses: {short_losses}, "
                       f"Trigger Days: {short_trigger_days})")

    # Calculate average daily capture and total capture for Buy and Short
    buy_avg_daily_capture = buy_returns[buy_returns != 0].mean() if buy_trigger_days > 0 else 0
    short_avg_daily_capture = short_returns[short_returns != 0].mean() if short_trigger_days > 0 else 0
    
    avg_daily_capture_buy = f"Buy Avg. Daily Capture: {buy_avg_daily_capture * 100:.4f}%"
    avg_daily_capture_short = f"Short Avg. Daily Capture: {short_avg_daily_capture * 100:.4f}%"
    
    total_capture_buy = f"Buy Total Capture: {total_buy_capture.iloc[-1] * 100:.4f}%" if len(total_buy_capture) > 0 else "Buy Total Capture: N/A"
    total_capture_short = f"Short Total Capture: {total_short_capture.iloc[-1] * 100:.4f}%" if len(total_short_capture) > 0 else "Short Total Capture: N/A"

    return fig, trigger_days_buy, win_ratio_buy, avg_daily_capture_buy, total_capture_buy, trigger_days_short, win_ratio_short, avg_daily_capture_short, total_capture_short

@app.callback(
    Output('update-interval', 'disabled'),
    [Input('ticker-input', 'value'),
     Input('update-interval', 'n_intervals')]
)
def disable_interval_when_data_loaded(ticker, n_intervals):
    if not ticker:
        return True  # Disable interval when no ticker is entered

    results = load_precomputed_results(ticker)
    if results is None:
        return False  # Keep interval running
    else:
        return True  # Disable interval once data is loaded
    
if __name__ == "__main__":
    # Run the Dash app
    app.run_server(debug=True)