import yfinance as yf
import plotly.graph_objects as go
from dash import Dash
import dash.dcc as dcc
import dash.html as html
from dash.dependencies import Input, Output, State
import dash_bootstrap_components as dbc
import pandas as pd
from functools import lru_cache
import pickle
from tqdm import tqdm
import os
import time
import json
from pprint import pprint
import shutil

MAX_SMA_DAY = 300

def fetch_data(ticker):
    try:
        df = yf.download(ticker, period='max', interval='1d', progress=False)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as e:
        print(f"Failed to fetch data for {ticker}: {type(e).__name__} - {str(e)}")
        return pd.DataFrame()

def identify_new_trading_days(df, existing_data):
    if existing_data is not None and not existing_data.empty:
        latest_existing_date = existing_data.index.max()
        new_trading_days = df.loc[latest_existing_date:].iloc[1:]
        if not new_trading_days.empty:
            if len(new_trading_days) == 1:
                print(f"New trading day found: {new_trading_days.index[0]}")
            else:
                print(f"New trading days found: {new_trading_days.index.min()} to {new_trading_days.index.max()}")
            return new_trading_days
    return None

def check_and_compute_missing_smas(df, MAX_SMA_DAY, existing_max_sma_day, total_trading_days):
    print("Inside check_and_compute_missing_smas...")
    print(f"MAX_SMA_DAY: {MAX_SMA_DAY}")
    print(f"existing_max_sma_day: {existing_max_sma_day}")
    print(f"total_trading_days: {total_trading_days}")
    print("DataFrame before modifications:")
    print()

    # Print the first 5 rows
    print("First 5 rows of the DataFrame:")
    print(df.head(5))
    print()

    # Print the last 5 rows
    print("Last 5 rows of the DataFrame:")
    print(df.tail(5))

    # Identify any missing SMA columns based on the expected range, limiting to the total trading days
    missing_columns = [f'SMA_{i}' for i in range(existing_max_sma_day + 1, min(MAX_SMA_DAY, total_trading_days) + 1) if f'SMA_{i}' not in df.columns]

    # Check if there are any missing SMA columns
    if missing_columns:
        print()
        print(f"Computing missing SMA columns: {missing_columns[0]} to {missing_columns[-1]}") if missing_columns else None

        # Compute all missing SMA columns at once
        missing_sma_data = {
            f'SMA_{window_size}': df['Close'].rolling(window=window_size, min_periods=window_size).mean()
            for window_size in range(existing_max_sma_day + 1, min(MAX_SMA_DAY, total_trading_days) + 1)
            if total_trading_days >= window_size
        }

        # Concatenate the missing SMA columns with the original DataFrame
        df = pd.concat([df, pd.DataFrame(missing_sma_data)], axis=1)

        print(f"Finished computing {missing_columns[0]} to {missing_columns[-1]}") if missing_columns else None

        # Create a copy of the DataFrame to get a defragmented frame
        df = df.copy()

    else:
        print()
        print("No missing SMA columns found.")

    print("DataFrame after modifications:")
    print()
    
    print("First 5 rows of the DataFrame:")
    print(df.head(5))
    print()

    print("Last 5 rows of the DataFrame:")
    print(df.tail(5))
    print()
    print("Finished check_and_compute_missing_smas.")
    
    return df

def update_sma_and_captures(existing_data, new_trading_days, MAX_SMA_DAY, sma_pairs, buy_results, short_results):
    print("Inside update_sma_and_captures...")

    if new_trading_days is not None:
        print(f"Concatenating existing_data and new_trading_days...")
        updated_data = pd.concat([existing_data, new_trading_days])
        print(f"Checking and computing missing SMAs...")
        updated_data = check_and_compute_missing_smas(updated_data, MAX_SMA_DAY, len(existing_data))

    # If MAX_SMA_DAY has expanded, calculate new SMA pairs
    if MAX_SMA_DAY > len(existing_data):
        print(f"Calculating new SMA pairs...")
        # Add your code here to calculate new SMA pairs

    # If there are new trading days, update SMA columns in a rolling window fashion
    if new_trading_days is not None:
        print(f"Updating SMA columns for new trading days...")
        
        # Update capture calculations for each SMA trading pair
        print(f"Updating capture calculations for each SMA trading pair...")
        for pair in tqdm(sma_pairs, desc="Processing pairs", dynamic_ncols=True):
            if f'SMA_{pair[0]}' in updated_data.columns and f'SMA_{pair[1]}' in updated_data.columns:
                sma1 = updated_data[f'SMA_{pair[0]}']
                sma2 = updated_data[f'SMA_{pair[1]}']
                
                buy_signals = sma1 > sma2
                buy_returns = updated_data['Close'].pct_change().where(buy_signals.shift(1, fill_value=False), 0)
                buy_capture = buy_returns.cumsum()
                
                short_signals = sma1 < sma2
                short_returns = -updated_data['Close'].pct_change().where(short_signals.shift(1, fill_value=False), 0)
                short_capture = short_returns.cumsum()
                
                # Update buy_results and short_results dictionaries with the latest capture values
                buy_results[pair] = buy_capture.iloc[-1]
                short_results[pair] = short_capture.iloc[-1]
            else:
                print(f"Missing SMA columns for pair {pair}. Skipping calculations.")

    else:
        print("No new trading days. Updating existing_data...")
        updated_data = existing_data
        
        # Recalculate buy and short captures for existing SMA pairs
        print(f"Recalculating buy and short captures for existing SMA pairs...")
        for pair in tqdm(sma_pairs, desc="Processing pairs", dynamic_ncols=True):
            if f'SMA_{pair[0]}' in updated_data.columns and f'SMA_{pair[1]}' in updated_data.columns:
                sma1 = updated_data[f'SMA_{pair[0]}']
                sma2 = updated_data[f'SMA_{pair[1]}']
                
                buy_signals = sma1 > sma2
                buy_returns = updated_data['Close'].pct_change().where(buy_signals.shift(1, fill_value=False), 0)
                buy_capture = buy_returns.cumsum()
                
                short_signals = sma1 < sma2
                short_returns = -updated_data['Close'].pct_change().where(short_signals.shift(1, fill_value=False), 0)
                short_capture = short_returns.cumsum()
                
                # Update buy_results and short_results dictionaries with the latest capture values
                buy_results[pair] = buy_capture.iloc[-1]
                short_results[pair] = short_capture.iloc[-1]
            else:
                print(f"Missing SMA columns for pair {pair}. Skipping calculations.")

    print("Finished update_sma_and_captures.")
    return updated_data, buy_results, short_results

def preprocess_data(df, MAX_SMA_DAY, existing_max_sma_day, total_trading_days):
    print("Preprocessing data...")
    
    # Fill NaN values in 'Close' column to avoid propagation in SMA calculations
    df['Close'] = df['Close'].ffill()

    print("Columns before adding new SMA columns:")
    print(df.columns)
    print()
    
    # Compute new SMA columns
    new_sma_columns = {f'SMA_{day}': df['Close'].rolling(window=day).mean() for day in range(existing_max_sma_day + 1, min(MAX_SMA_DAY, total_trading_days) + 1)}
    df = pd.concat([df, pd.DataFrame(new_sma_columns)], axis=1)

    print("Columns after adding new SMA columns:")
    print(df.columns)
    print()
    
    sma_combinations = [(i, j) for i in range(1, min(MAX_SMA_DAY, total_trading_days) + 1) for j in range(i + 1, min(MAX_SMA_DAY, total_trading_days) + 1)]
    df.attrs['sma_combinations'] = sma_combinations
    
    print("Preprocessing complete.")
    
    return df, sma_combinations

from functools import lru_cache
import os
import time

def get_last_modified_time(file_path):
    if os.path.exists(file_path):
        return os.path.getmtime(file_path)
    return None

@lru_cache(maxsize=None)
def get_data(ticker, MAX_SMA_DAY, is_precomputing=False, cache_timestamp=None):
    try:
        print()
        print()
        print(f"get_data called for {ticker} with MAX_SMA_DAY {MAX_SMA_DAY}")
        pkl_file = f'{ticker}_precomputed_results.pkl'
        new_trading_days = None  # Initialize new_trading_days to None

        # Get the last modified timestamp of the pickle file
        last_modified_time = get_last_modified_time(pkl_file)

        if cache_timestamp is None or last_modified_time != cache_timestamp:
            # Invalidate the cache if the pickle file has been updated
            get_data.cache_clear()
            print("Cache invalidated due to updated pickle file.")

        if os.path.exists(pkl_file):
            try:
                with open(pkl_file, 'rb') as file:
                    results = pickle.load(file)
                    df, sma_combinations = results.get('preprocessed_data', (None, None))
                    existing_max_sma_day = results.get('existing_max_sma_day', 0)
                    print(f"Loaded existing_max_sma_day: {existing_max_sma_day}")

                    if df is not None:
                        print(f"Columns in the loaded DataFrame: {list(df.columns[:2])} ... {list(df.columns[-2:])}")
                    else:
                        print("DataFrame is None")

                    pprint(f"SMA combinations: {sma_combinations[:2]} ... {sma_combinations[-2:]}")

                    # Get the total trading days from the loaded DataFrame
                    total_trading_days_loaded = len(df) if df is not None else 0
                    print(f"Total trading days (from loaded DataFrame): {total_trading_days_loaded}")

            except (pickle.UnpicklingError, EOFError) as e:
                print(f"Error loading precomputed results for {ticker}: {str(e)}")
                results = {}  # Initialize results as an empty dictionary only if there's an error loading the pickle file
                existing_max_sma_day = 0  # Set existing_max_sma_day to 0 if there's an error loading the pickle file
                df = None  # Set df to None if there's an error loading the pickle file

        else:
            results = {}  # Initialize results as an empty dictionary if the pickle file doesn't exist
            existing_max_sma_day = 0  # Set existing_max_sma_day to 0 if the pickle file doesn't exist
            df = None  # Set df to None if the pickle file doesn't exist

        print(f"Fetching data for {ticker}...")
        fetched_df = fetch_data(ticker)

        if fetched_df is not None and not fetched_df.empty:
            if df is None:
                # If df is None, use the fetched DataFrame as the starting point
                df = fetched_df
            else:
                # Identify new trading days
                new_trading_days = identify_new_trading_days(fetched_df, df)

                if new_trading_days is not None:
                    # Update the existing DataFrame with new trading days
                    df = pd.concat([df, new_trading_days])

            # Get the total trading days from the updated DataFrame
            total_trading_days = len(df)
            print(f"Total trading days (from fetched DataFrame): {total_trading_days}")

            print("Columns before updating SMA columns:")
            print(df.columns)
            print()

            if existing_max_sma_day < min(MAX_SMA_DAY, total_trading_days):
                print(f"existing_max_sma_day is less than the minimum of MAX_SMA_DAY and total_trading_days. Computing missing SMA columns.")
                df = check_and_compute_missing_smas(df, min(MAX_SMA_DAY, total_trading_days), existing_max_sma_day, total_trading_days)

                print("Columns after updating SMA columns:")
                print(df.columns)
                print()

                sma_combinations = [(i, j) for i in range(1, min(MAX_SMA_DAY, total_trading_days) + 1) for j in range(i + 1, min(MAX_SMA_DAY, total_trading_days) + 1)]

                # Save the updated DataFrame back to the pickle file
                results['preprocessed_data'] = (df, sma_combinations)
                with open(pkl_file, 'wb') as file:
                    pickle.dump(results, file)

                pprint(f"Updated SMA combinations: {sma_combinations[:2]} ... {sma_combinations[-2:]}")
                print(f"Updated DataFrame with missing SMA columns and saved to pickle file.")
            else:
                print(f"existing_max_sma_day is equal to or greater than the minimum of MAX_SMA_DAY and total_trading_days. No missing SMA columns to compute.")

            pprint(f"Columns in the DataFrame after updating SMA columns: {list(df.columns[:2])} ... {list(df.columns[-2:])}")
            needs_precompute = (MAX_SMA_DAY > existing_max_sma_day) or (new_trading_days is not None)
            return df, sma_combinations, True, MAX_SMA_DAY, existing_max_sma_day, needs_precompute, new_trading_days, last_modified_time

        else:
            print(f"Failed to fetch data for {ticker}. Skipping preprocessing.")
            return None, None, False, 0, 0, False, new_trading_days, last_modified_time  # Return default values instead of None

    except Exception as e:
        print(f"An error occurred in get_data: {str(e)}")
        return None, None, False, 0, 0, False, None, None  # Return default values in case of an error

@lru_cache(maxsize=None)
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
    with open(status_path, 'w') as f:
        json.dump(status, f)

def precompute_results(ticker, df, sma_combinations, MAX_SMA_DAY, existing_max_sma_day):
    print(f"precompute_results called for {ticker} with MAX_SMA_DAY {MAX_SMA_DAY}")
    results = {}
    top_buy_pair = None
    top_short_pair = None
    print(f"Processing ticker: {ticker}")
    pkl_file = f'{ticker}_precomputed_results.pkl'
    pkl_backup_file = f'{ticker}_precomputed_results_backup.pkl'  # Add this line

    # Load existing results
    if os.path.exists(pkl_file):
        with open(pkl_file, 'rb') as file:
            existing_results = pickle.load(file)
            if not existing_results:  # Check if the dictionary is empty
                existing_results = None
            else:
                top_buy_pair = existing_results.get('top_buy_pair')
                top_short_pair = existing_results.get('top_short_pair')
                print(f"Loaded existing results for {ticker}")
                print(f"Existing top buy pair: {top_buy_pair}")
                print(f"Existing top short pair: {top_short_pair}")
    else:
        existing_results = None
        print(f"No existing results found for {ticker}")

    try:
        if df is None or df.empty:
            write_status(ticker, {"status": "failed", "message": "No data"})
            return None
        print(f"Data fetched and preprocessed for {ticker}.")

        # Create a backup of the existing pickle file before starting the calculation
        if os.path.exists(pkl_file):
            shutil.copy(pkl_file, pkl_backup_file)

        min_date = df.index.min()
        start_date = min_date.strftime('%Y-%m-%d') if pd.notnull(min_date) else 'No date available'
        print(f"Start date for {ticker}: {start_date}")

        if existing_results:
            existing_buy_results = existing_results.get('buy_results', {})
            existing_short_results = existing_results.get('short_results', {})
            print(f"Loaded existing_max_sma_day: {existing_max_sma_day}")
        else:
            existing_buy_results = {}
            existing_short_results = {}

        print(f"MAX_SMA_DAY: {MAX_SMA_DAY}")
        print(f"existing_max_sma_day: {existing_max_sma_day}")

        buy_results = existing_buy_results
        short_results = existing_short_results

        # Get the total trading days
        total_trading_days = len(df)
        print(f"Total trading days: {total_trading_days}")

        # Calculate adjusted_max_sma_day
        adjusted_max_sma_day = min(MAX_SMA_DAY, total_trading_days)
        print(f"Adjusted max SMA day: {adjusted_max_sma_day}")

        new_sma_pairs = []  # Initialize new_sma_pairs as an empty list

        if MAX_SMA_DAY > existing_max_sma_day:
            print(f"Performing brute-force calculation for new SMA pairs up to adjusted max SMA day.")
            # Perform brute-force calculations for the new SMA pairs that are not already calculated
            new_sma_pairs = [(i, j) for i in range(1, adjusted_max_sma_day) for j in range(i + 1, adjusted_max_sma_day + 1) if j > existing_max_sma_day]

            # If there are no new SMA pairs, create a pair with the previous SMA day and the adjusted max SMA day
            if len(new_sma_pairs) == 0:
                new_sma_pairs = [(existing_max_sma_day, adjusted_max_sma_day)]
        else:
            print(f"No new SMA pairs to calculate for {ticker}")

        print(f"New SMA pairs: {new_sma_pairs[:2]} ... {new_sma_pairs[-2:]}")

        if new_sma_pairs:
            print(f"Starting brute-force calculation for {ticker} with new SMA pairs")
            with tqdm(total=len(new_sma_pairs), desc='Brute-Force Calculation', unit='pair', dynamic_ncols=True, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]') as pbar:
                for pair in new_sma_pairs:
                    try:
                        sma1 = df[f'SMA_{pair[0]}']
                        sma2 = df[f'SMA_{pair[1]}']

                        # Calculate buy capture for the new SMA pair
                        buy_signals = sma1 > sma2
                        buy_returns = df['Close'].pct_change().where(buy_signals.shift(1, fill_value=False), 0)
                        buy_capture = buy_returns.sum()
                        buy_results[pair] = buy_capture

                        # Calculate short capture for the new SMA pair
                        short_signals = sma1 < sma2
                        short_returns = -df['Close'].pct_change().where(short_signals.shift(1, fill_value=False), 0)
                        short_capture = short_returns.sum()
                        short_results[pair] = short_capture

                    except KeyError as e:
                        print(f"KeyError occurred for pair {pair}: {str(e)}")
                    except Exception as e:
                        print(f"Error occurred for pair {pair}: {str(e)}")

                    pbar.update(1)

            print(f"Finished brute-force calculation for {ticker} with new SMA pairs")
        else:
            print(f"No new SMA pairs to calculate for {ticker}.")

        latest_existing_date = df.index.max()
        print(f"Date of last brute-force calculation up through SMA_{min(adjusted_max_sma_day, total_trading_days)}: {latest_existing_date}")

        print(f"Updated buy_results Range: {dict(list(buy_results.items())[:1])} ... {dict(list(buy_results.items())[-1:])}")
        print(f"Updated short_results Range: {dict(list(short_results.items())[:1])} ... {dict(list(short_results.items())[-1:])}")
        print()

        # Create separate dictionaries for buy and short results, including the inverted pairs
        buy_results_with_inverse = {**buy_results, **{(pair[1], pair[0]): -result for pair, result in short_results.items()}}
        short_results_with_inverse = {**short_results, **{(pair[1], pair[0]): -result for pair, result in buy_results.items()}}

        # Identify the top performing buy and short pairs from the respective dictionaries
        top_buy_pair = max(buy_results_with_inverse, key=lambda x: buy_results_with_inverse[x]) if buy_results_with_inverse else None
        top_short_pair = max(short_results_with_inverse, key=lambda x: short_results_with_inverse[x]) if short_results_with_inverse else None

        # Print the top pairs along with their results
        if top_buy_pair is not None:
            print(f"Top Buy Pair for {ticker}: {top_buy_pair} with result {buy_results_with_inverse[top_buy_pair]}")
        if top_short_pair is not None:
            print(f"Top Short Pair for {ticker}: {top_short_pair} with result {short_results_with_inverse[top_short_pair]}")
            print()

        # Update existing_max_sma_day in the results dictionary after brute-force calculations
        results['existing_max_sma_day'] = adjusted_max_sma_day

        # Save the results
        results = {
            'top_buy_pair': top_buy_pair,
            'top_short_pair': top_short_pair,
            'buy_results': buy_results,
            'short_results': short_results,
            'start_date': df.index.min().strftime('%Y-%m-%d'),
            'preprocessed_data': (df, sma_combinations),
            'total_trading_days': total_trading_days,
            'existing_max_sma_day': adjusted_max_sma_day  # Include existing_max_sma_day in the results dictionary
        }

        print(f"Saving results to {pkl_file}")

        try:
            with open(pkl_file, 'wb') as file:
                pickle.dump(results, file)
            print("Results saved successfully.")
            write_status(ticker, {"status": "complete", "progress": 100})
        except Exception as e:
            error_message = f"Error in precompute_results for {ticker}: {str(e)}"
            print(f"Error occurred while saving results to {pkl_file}: {str(e)}")
            print(error_message)
            write_status(ticker, {"status": "failed", "message": error_message})

    except Exception as e:
        print(f"An error occurred: {str(e)}")
        write_status(ticker, {"status": "failed", "message": str(e)})

        # Restore the backup file if it exists
        if os.path.exists(pkl_backup_file):
            shutil.copy(pkl_backup_file, pkl_file)
            print("Backup file restored.")

        return None

    return results

# Load the precomputed results for the default ticker
def load_precomputed_results(ticker):
    pkl_file = f'{ticker}_precomputed_results.pkl'
    if os.path.exists(pkl_file):
        with open(pkl_file, 'rb') as file:
            results = pickle.load(file)
            
            # Check if 'top_buy_pair' and 'top_short_pair' are present in the results
            if 'top_buy_pair' not in results:
                results['top_buy_pair'] = None
            if 'top_short_pair' not in results:
                results['top_short_pair'] = None
            
            # Check if 'existing_max_sma_day' is present in the results
            if 'existing_max_sma_day' not in results:
                # If 'MAX_SMA_DAY' is present, rename it to 'existing_max_sma_day'
                if 'MAX_SMA_DAY' in results:
                    results['existing_max_sma_day'] = results.pop('MAX_SMA_DAY')

            if 'existing_max_sma_day' not in results:
            # If 'max_sma_day' is present, rename it to 'existing_max_sma_day'
                if 'max_sma_day' in results:
                    results['existing_max_sma_day'] = results.pop('max_sma_day')

                    # Save the updated results back to the pickle file
                    with open(pkl_file, 'wb') as file:
                        pickle.dump(results, file)
                        print(f"Updated pickle file for {ticker} with 'existing_max_sma_day'")
                        
            return results
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
        top_buy_pair = {}
        top_short_pair = {}
        buy_results = {}
        short_results = {} 

    return top_buy_pair, top_short_pair, buy_results, short_results

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

# Call the function and print the status
status = read_status('VIK')  # replace 'AAPL' with your ticker
print(status)

# Update your Dash layout to include the status and interval component
app.layout = html.Div(
    style={
        'background-color': 'black',
        'color': '#00FF00',
        'font-family': 'Courier New, monospace'
    },
    children=[
        html.H1('SMA Trading Pair Analysis', className='text-center mt-3'),
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
        dcc.Interval(id='update-interval', interval=5000, n_intervals=0),  # Update every 5 seconds
        dcc.Loading(
            id="loading-1",
            type="default",
            children=html.Div(id="loading-output")
        )
    ]
)

# Callback to update the labels of the SMA day inputs
@app.callback(
    [Output('sma-input-1', 'max'),
     Output('sma-input-2', 'max'),
     Output('sma-input-3', 'max'),
     Output('sma-input-4', 'max'),
     Output('sma-input-1-label', 'children'),
     Output('sma-input-2-label', 'children'),
     Output('sma-input-3-label', 'children'),
     Output('sma-input-4-label', 'children')],
    [Input('ticker-input', 'value')]
)
def update_sma_labels(ticker):
    if not ticker:
        trading_days = 1
    else:
        df = fetch_data(ticker)
        if df is None or df.empty:
            trading_days = 1
        else:
            trading_days = len(df)

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
    Input('update-interval', 'n_intervals'),
    State('ticker-input', 'value')
)
def update_status(n_intervals, ticker):
    if ticker:
        status = read_status(ticker)
        if status['status'] == 'processing':
            return f"Processing {ticker}... {status['progress']}% completed"
        elif status['status'] == 'complete':
            return f"Processing complete for {ticker}!"
        elif status['status'] == 'failed':
            return f"Failed to process {ticker}: {status.get('message', '')}"
    return "Enter a ticker and press submit to start processing."

# Callback to toggle the visibility of the Calculation Components section
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

# validate_sma_inputs callback
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
        if df is None or df.empty:
            trading_days = 1
        else:
            trading_days = len(df)
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

    # Always call get_data first
    df, sma_combinations, _, MAX_SMA_DAY, existing_max_sma_day, needs_precompute, new_trading_days, _ = get_data(ticker, MAX_SMA_DAY)

    if df is None or df.empty:
        return ''

    # Trigger precomputation if needed
    if needs_precompute or (new_trading_days is not None):
        print(f"Triggering precomputation for {ticker}")
        precompute_results(ticker, df, sma_combinations, MAX_SMA_DAY, existing_max_sma_day)

    return ''

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
    [Input('ticker-input', 'value')]
)
def update_dynamic_strategy_display(ticker):
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

    print()
    print(f"Loaded top_buy_pair: {top_buy_pair} with result {buy_results.get(top_buy_pair)}")
    print(f"Loaded top_short_pair: {top_short_pair} with result {short_results.get(top_short_pair)}")
    print()

    if top_buy_pair is None or top_short_pair is None:
        return ["Top pairs not found. Please check data integrity."] * 10

    # Check if 'preprocessed_data' is in results
    if 'preprocessed_data' not in results:
        print(f"'preprocessed_data' not found in results for {ticker}")
        return ["Data not available or processing not yet complete. Please wait..."] * 10

    # Use the cached data from the results
    df = results['preprocessed_data'][0]

    if f'SMA_{top_buy_pair[0]}' not in df.columns or f'SMA_{top_buy_pair[1]}' not in df.columns or \
    f'SMA_{top_short_pair[0]}' not in df.columns or f'SMA_{top_short_pair[1]}' not in df.columns:
        print(f"Required SMA columns not found in the DataFrame for {ticker}")
        return ["Data not available or processing not yet complete. Please wait..."] * 10

    try:
        sma1_buy_leader = df[f'SMA_{top_buy_pair[0]}']
        sma2_buy_leader = df[f'SMA_{top_buy_pair[1]}']
        buy_signals_leader = sma1_buy_leader > sma2_buy_leader
        buy_returns_leader = df['Close'].pct_change().where(buy_signals_leader.shift(1, fill_value=False), 0)

        sma1_short_leader = df[f'SMA_{top_short_pair[0]}']
        sma2_short_leader = df[f'SMA_{top_short_pair[1]}']
        short_signals_leader = sma1_short_leader < sma2_short_leader
        short_returns_leader = df['Close'].pct_change().where(short_signals_leader.shift(1, fill_value=False), 0) * -1

    except KeyError:
        print(f"Required SMA columns not found in the DataFrame for {ticker}")
        return ["Data not available or processing not yet complete. Please wait..."] * 10

    # Determine current trading direction based on active signals
    if buy_returns_leader.size > 0 and short_returns_leader.size > 0:
        if buy_returns_leader.sum() > short_returns_leader.sum():
            trading_direction = "Current Trading Direction: Buy (Both triggers active, Buy leading)"
            active_leader_returns = buy_returns_leader
        else:
            trading_direction = "Current Trading Direction: Short (Both triggers active, Short leading)"
            active_leader_returns = short_returns_leader
    elif buy_returns_leader.size > 0:
        trading_direction = "Current Trading Direction: Buy"
        active_leader_returns = buy_returns_leader
    elif short_returns_leader.size > 0:
        trading_direction = "Current Trading Direction: Short"
        active_leader_returns = short_returns_leader
    else:
        trading_direction = "Current Trading Direction: Cash (No active triggers)"
        active_leader_returns = pd.Series([0])

    most_productive_buy_pair_text = f"Most Productive Buy Pair: SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]}"
    most_productive_short_pair_text = f"Most Productive Short Pair: SMA {top_short_pair[0]} / SMA {top_short_pair[1]}"
    avg_capture_buy_leader = f"Avg. Capture % for Buy Leader: {buy_returns_leader.mean() * 100:.9f}%" if buy_returns_leader.size > 0 else "Avg. Capture % for Buy Leader: N/A"
    total_capture_buy_leader = f"Total Capture for Buy Leader: {buy_returns_leader.sum() * 100:.9f}%" if buy_returns_leader.size > 0 else "Total Capture for Buy Leader: N/A"
    avg_capture_short_leader = f"Avg. Capture % for Short Leader: {short_returns_leader.mean() * 100:.9f}%" if short_returns_leader.size > 0 else "Avg. Capture % for Short Leader: N/A"
    total_capture_short_leader = f"Total Capture for Short Leader: {short_returns_leader.sum() * 100:.9f}%" if short_returns_leader.size > 0 else "Total Capture for Short Leader: N/A"
    performance_expectation = f"Performance Expectation: {active_leader_returns.mean() * 100:.9f}%" if active_leader_returns.size > 0 else "Performance Expectation: N/A"
    confidence_percentage = f"Confidence Percentage: {(active_leader_returns > 0).mean() * 100:.9f}%" if active_leader_returns.size > 0 else "Confidence Percentage: N/A"

    # Generate trading recommendations based on the current leading SMA pairs
    buy_sma_slow = df[f'SMA_{top_buy_pair[0]}'].iloc[-1]
    short_sma_slow = df[f'SMA_{top_short_pair[0]}'].iloc[-1]

    buy_threshold = buy_sma_slow
    short_threshold = short_sma_slow

    buy_recommendation = f"Buy if {ticker} closes above {buy_threshold:.2f}" if trading_direction.startswith("Current Trading Direction: Buy") else "Buy: N/A"
    short_recommendation = f"Short if {ticker} closes below {short_threshold:.2f}" if trading_direction.startswith("Current Trading Direction: Short") else "Short: N/A"
    all_cash_recommendation = "Go All Cash" if trading_direction == "Current Trading Direction: Cash (No active triggers)" else "All Cash: N/A"

    trading_recommendations = [
        html.H6('Trading Recommendations for Next Day'),
        html.Div([
            html.P(f"Leading Buy SMA Pair: SMA {top_buy_pair[0]} / SMA {top_buy_pair[1]}"),
            html.P(buy_recommendation)
        ]),
        html.Div([
            html.P(f"Leading Short SMA Pair: SMA {top_short_pair[0]} / SMA {top_short_pair[1]}"),
            html.P(short_recommendation)
        ]),
        html.Div([
            html.P(all_cash_recommendation)
        ])
    ]

    return (
        most_productive_buy_pair_text,
        most_productive_short_pair_text,
        avg_capture_buy_leader,
        total_capture_buy_leader,
        avg_capture_short_leader,
        total_capture_short_leader,
        trading_direction,
        performance_expectation,
        confidence_percentage,
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
    if ticker is None or any(sma_day is None for sma_day in [sma_day_1, sma_day_2, sma_day_3, sma_day_4]):
        return go.Figure(), '', '', '', '', '', '', '', ''

    df = fetch_data(ticker)
    if df is None or df.empty:
        return go.Figure(), '', '', '', '', '', '', '', ''

    min_date = df.index.min()
    start_date = min_date.strftime('%Y-%m-%d') if pd.notnull(min_date) else 'No date available'

    sma1_buy = df['Close'].rolling(window=sma_day_1).mean()
    sma2_buy = df['Close'].rolling(window=sma_day_2).mean()
    buy_signals = sma1_buy > sma2_buy

    sma1_short = df['Close'].rolling(window=sma_day_3).mean()
    sma2_short = df['Close'].rolling(window=sma_day_4).mean()
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
        title=f'{ticker} Closing Prices, SMAs, and Total Capture (Start Date: {start_date})',
        xaxis_title='Trading Day',
        yaxis_title=f'{ticker} Closing Price',
        hovermode='x',
        uirevision='static',
        template='plotly_dark'
    )

    # Calculate trigger days for Buy and Short
    trigger_days_buy = f"Buy Trigger Days: {buy_signals.sum()}"
    trigger_days_short = f"Short Trigger Days: {short_signals.sum()}"

    # Calculate win ratios for Buy and Short
    buy_win_ratio = (buy_returns > 0).mean()
    short_win_ratio = (short_returns > 0).mean()
    win_ratio_buy = f"Buy Win Ratio: {buy_win_ratio * 100:.9f}%" if pd.notnull(buy_win_ratio) else "Buy Win Ratio: N/A"
    win_ratio_short = f"Short Win Ratio: {short_win_ratio * 100:.9f}%" if pd.notnull(short_win_ratio) else "Short Win Ratio: N/A"

    # Calculate average daily capture and total capture for Buy and Short
    buy_avg_daily_capture = buy_returns.mean()
    short_avg_daily_capture = short_returns.mean()
    avg_daily_capture_buy = f"Buy Avg. Daily Capture: {buy_avg_daily_capture * 100:.9f}%" if pd.notnull(buy_avg_daily_capture) else "Buy Avg. Daily Capture: N/A"
    avg_daily_capture_short = f"Short Avg. Daily Capture: {short_avg_daily_capture * 100:.9f}%" if pd.notnull(short_avg_daily_capture) else "Short Avg. Daily Capture: N/A"
    total_capture_buy = f"Buy Total Capture: {total_buy_capture.iloc[-1] * 100:.9f}%" if len(total_buy_capture) > 0 and pd.notnull(total_buy_capture.iloc[-1]) else "Buy Total Capture: N/A"
    total_capture_short = f"Short Total Capture: {total_short_capture.iloc[-1] * 100:.9f}%" if len(total_short_capture) > 0 and pd.notnull(total_short_capture.iloc[-1]) else "Short Total Capture: N/A"

    return fig, trigger_days_buy, win_ratio_buy, avg_daily_capture_buy, total_capture_buy, trigger_days_short, win_ratio_short, avg_daily_capture_short, total_capture_short

if __name__ == "__main__":
    # Run the Dash app
    app.run_server(debug=True)