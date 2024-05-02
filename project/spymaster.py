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
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
import os
import json

MAX_SMA_DAY = 3

@lru_cache(maxsize=None)
def fetch_data(ticker, MAX_SMA_DAY):
    try:
        df = yf.download(ticker, period='max', interval='1d')
        return df
    except Exception as e:
        print(f"Failed to fetch data for {ticker}: {e}")
        return None

@lru_cache(maxsize=None)
def get_data(ticker, MAX_SMA_DAY):
    print(f"get_data called for {ticker} with MAX_SMA_DAY {MAX_SMA_DAY}")
    pkl_file = f'{ticker}_precomputed_results.pkl'
    if os.path.exists(pkl_file):
        with open(pkl_file, 'rb') as file:
            results = pickle.load(file)
            df, sma_combinations = results.get('preprocessed_data', (None, None))
            if df is not None and sma_combinations is not None:
                print(f"Loaded preprocessed data for {ticker} from {pkl_file}")
                
                # Check if MAX_SMA_DAY is greater than existing_max_sma_day
                existing_max_sma_day = results.get('max_sma_day', 0)
                if MAX_SMA_DAY > existing_max_sma_day:
                    print(f"Updating preprocessed data for {ticker} with new SMA columns up to MAX_SMA_DAY {MAX_SMA_DAY}")
                    
                    # Compute new SMA columns
                    new_sma_columns = {f'SMA_{day}': df['Close'].rolling(window=day).mean() for day in range(existing_max_sma_day + 1, MAX_SMA_DAY + 1)}
                    print(f"New SMA columns: {list(new_sma_columns.keys())}")
                    df = pd.concat([df, pd.DataFrame(new_sma_columns)], axis=1)
                    
                    # Remove duplicate SMA columns
                    df = df.loc[:, ~df.columns.duplicated()]
                    
                    # Update sma_combinations
                    sma_combinations = [(i, j) for i in range(1, MAX_SMA_DAY + 1) for j in range(i + 1, MAX_SMA_DAY + 1)]
                    print(f"Updated sma_combinations: {sma_combinations}")
                    
                    # Update preprocessed_data in the results dictionary
                    results['preprocessed_data'] = (df, sma_combinations)
                    results['max_sma_day'] = MAX_SMA_DAY
                    
                    # Save the updated results back to the pkl file
                    with open(pkl_file, 'wb') as file:
                        pickle.dump(results, file)
                    
                    print(f"Preprocessed data for {ticker} updated with new SMA columns up to MAX_SMA_DAY {MAX_SMA_DAY}")
                
                return df, sma_combinations, True  # Return True to indicate data loaded from pickle
    
    print(f"No preprocessed data found for {ticker}. Fetching data and preprocessing...")
    df = fetch_data(ticker, MAX_SMA_DAY)
    if df is not None and not df.empty:
        df, sma_combinations = preprocess_data(df, MAX_SMA_DAY)
        return df, sma_combinations, False  # Return False to indicate data not loaded from pickle
    
    return None, None, False

def fetch_data_for_tickers(tickers):
    results = {}
    with ThreadPoolExecutor() as executor:
        futures = [executor.submit(fetch_data, ticker) for ticker in tickers]
        for future in as_completed(futures):
            ticker = tickers[futures.index(future)]
            results[ticker] = future.result()
    return results

def preprocess_data(df, MAX_SMA_DAY):
    print("Preprocessing data...")

    # Only add each SMA column once
    sma_columns = {f'SMA_{day}': df['Close'].rolling(window=day).mean() for day in range(1, MAX_SMA_DAY + 1)}
    df = pd.concat([df, pd.DataFrame(sma_columns)], axis=1)

    # Remove duplicate SMA columns
    df = df.loc[:, ~df.columns.duplicated()]

    # Compute sma_combinations
    sma_combinations = [(i, j) for i in range(1, MAX_SMA_DAY + 1) for j in range(i + 1, MAX_SMA_DAY + 1)]

    print("Preprocessing complete.")
    print("Preprocessed DataFrame:")

    return df, sma_combinations

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

def precompute_results(ticker, MAX_SMA_DAY):
    print(f"Processing ticker: {ticker}")  # Print the ticker being processed
    pkl_file = f'{ticker}_precomputed_results.pkl'

    # Load existing results
    if os.path.exists(pkl_file):
        with open(pkl_file, 'rb') as file:
            existing_results = pickle.load(file)
            existing_buy_results = existing_results.get('buy_results', {})
            existing_short_results = existing_results.get('short_results', {})
            existing_max_sma_day = existing_results.get('max_sma_day', 0)
            print(f"Loaded existing results for {ticker}")
            print(f"Existing top buy pair: {existing_results.get('top_buy_pair')}")
            print(f"Existing top short pair: {existing_results.get('top_short_pair')}")
    else:
        existing_buy_results = {}
        existing_short_results = {}
        existing_max_sma_day = 0
        print(f"No existing results found for {ticker}")

    try:
        print("Fetching data...")
        df, sma_combinations, loaded_from_pickle = get_data(ticker, MAX_SMA_DAY)
        if df is None or df.empty:
            write_status(ticker, {"status": "failed", "message": "No data"})
            return None
        print(f"Data fetched for {ticker}.")

        start_date = df.index.min().strftime('%Y-%m-%d')
        print(f"Start date for {ticker}: {start_date}")
        print(f"End date: {df.index.max()}")

        if not loaded_from_pickle or MAX_SMA_DAY > existing_max_sma_day:
            print("Performing dynamic trading strategy calculations...")
            buy_results = existing_buy_results
            short_results = existing_short_results

            sma_pairs = [(i, j) for i in range(1, MAX_SMA_DAY + 1) for j in range(1, MAX_SMA_DAY + 1) if i != j]

            print(f"Starting brute-force calculation for {ticker}")
            with tqdm(total=len(sma_pairs), desc='Brute-Force Calculation', unit='pair', ncols=80, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]') as pbar:
                for pair in sma_pairs:
                    try:
                        sma1 = df[f'SMA_{pair[0]}']
                        sma2 = df[f'SMA_{pair[1]}']

                        # Calculate buy capture
                        buy_signals = sma1 > sma2
                        buy_returns = df['Close'].pct_change().where(buy_signals.shift(1, fill_value=False), 0)
                        buy_capture = buy_returns.cumsum()
                        buy_results[pair] = buy_capture.iloc[-1]

                        # Calculate short capture (inverse logic for short)
                        short_signals = sma1 < sma2
                        short_returns = -df['Close'].pct_change().where(short_signals.shift(1, fill_value=False), 0)
                        short_capture = short_returns.cumsum()
                        short_results[pair] = short_capture.iloc[-1]

                    except KeyError as e:
                        print(f"KeyError occurred for pair {pair}: {str(e)}")
                    except Exception as e:
                        print(f"Error occurred for pair {pair}: {str(e)}")

                    pbar.update(1)

            print(f"Finished brute-force calculation for {ticker}")
            print(f"Buy Results for {ticker}: {buy_results}")
            print(f"Short Results for {ticker}: {short_results}")

            # Identify the top performing buy and the poorest performing buy as the top performing short -- inverted elsewhere in script
            top_buy_pair = max(buy_results, key=buy_results.get)
            top_short_pair = min(short_results, key=short_results.get)

            print(f"Top Buy Pair for {ticker}: {top_buy_pair}")
            print(f"Top Short Pair for {ticker}: {top_short_pair}")

            results = {
                'top_buy_pair': top_buy_pair,
                'top_short_pair': top_short_pair,
                'buy_results': buy_results,
                'short_results': short_results,
                'start_date': start_date,
                'max_sma_day': MAX_SMA_DAY,
                'preprocessed_data': (df, sma_combinations)  # Save the DataFrame and sma_combinations to the results
            }

            print(f"Results for {ticker}: {results}")
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
        else:
            print(f"Data loaded from pickle file for {ticker}. Skipping brute-force calculation.")

    except Exception as e:
        print(f"An error occurred: {str(e)}")

precompute_results('^GSPC', MAX_SMA_DAY)  # Example ticker: S&P 500 index

# Load the precomputed results
def load_precomputed_results(ticker):
    pkl_file = f'{ticker}_precomputed_results.pkl'
    if os.path.exists(pkl_file):
        with open(pkl_file, 'rb') as file:
            results = pickle.load(file)
            return results
    else:
        return None

# Load the precomputed results for the default ticker
default_ticker = '^GSPC'
precomputed_results = load_precomputed_results(default_ticker)

if precomputed_results is not None:
    top_buy_pair = precomputed_results['top_buy_pair']
    top_short_pair = precomputed_results['top_short_pair']
else:
    # Set default values if precomputed results are not available
    top_buy_pair = (1, 3)
    top_short_pair = (20, 5)

# Initialize the Dash app with a dark theme and custom styles
app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])

# Function to read the processing status from a file
def read_status(ticker):
    status_path = f"{ticker}_status.json"
    if os.path.exists(status_path):
        with open(status_path, 'r') as file:
            return json.load(file)
    return {"status": "not started", "progress": 0}

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
                            html.Label(f'Enter 1st SMA Day (1-{MAX_SMA_DAY}) for Buy Pair:', className='mb-1'),
                            dcc.Input(id='sma-input-1', type='number', min=1, max=MAX_SMA_DAY, step=1, className='form-control'),
                            html.Div(id='sma-input-1-error', className='text-danger')
                        ], className='mb-3'),
                        html.Div([
                            html.Label(f'Enter 2nd SMA Day (1-{MAX_SMA_DAY}) for Buy Pair:', className='mb-1'),
                            dcc.Input(id='sma-input-2', type='number', min=1, max=MAX_SMA_DAY, step=1, className='form-control'),
                            html.Div(id='sma-input-2-error', className='text-danger')
                        ], className='mb-3')
                    ])
                ], className='mb-3'),
                dbc.Card([
                    dbc.CardHeader('Short Pair'),
                    dbc.CardBody([
                        html.Div([
                            html.Label(f'Enter 3rd SMA Day (1-{MAX_SMA_DAY}) for Short Pair:', className='mb-1'),
                            dcc.Input(id='sma-input-3', type='number', min=1, max=MAX_SMA_DAY, step=1, className='form-control'),
                            html.Div(id='sma-input-3-error', className='text-danger')
                        ], className='mb-3'),
                        html.Div([
                            html.Label(f'Enter 4th SMA Day (1-{MAX_SMA_DAY}) for Short Pair:', className='mb-1'),
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

# Ensure callback IDs match exactly what's in the layout and are correctly specified.
# For instance:
@app.callback(
    Output('processing-status', 'children'),
    Input('update-interval', 'n_intervals'),
    State('ticker-input', 'value'))
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

# Callback to validate SMA input fields
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
     Input('sma-input-4', 'value')]
)
def validate_sma_inputs(sma_input_1, sma_input_2, sma_input_3, sma_input_4):
    sma_inputs = [sma_input_1, sma_input_2, sma_input_3, sma_input_4]
    input_classes = []
    error_messages = []

    for sma_input in sma_inputs:
        if sma_input is None or sma_input < 1 or sma_input > MAX_SMA_DAY:
            input_classes.append('form-control is-invalid')
            error_messages.append('Please enter a valid SMA day (1-{MAX_SMA_DAY}).')
        else:
            input_classes.append('form-control')
            error_messages.append('')

    return input_classes + error_messages

@app.callback(
    Output('ticker-input-feedback', 'children'),
    [Input('ticker-input', 'value')]
)
def validate_ticker_input(ticker):
    if not ticker:
        return ''
    df, _, loaded_from_pickle = get_data(ticker, MAX_SMA_DAY)
    
    if df is None or df.empty:
        return go.Figure()

    pkl_file = f'{ticker}_precomputed_results.pkl'
    # Use pkl_file as needed...

    # Trigger precomputation if the pickle file doesn't exist or if data was not loaded from pickle
    if not loaded_from_pickle:
        precompute_results(ticker, MAX_SMA_DAY)

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
    print(f"Loaded precomputed results for {ticker}")  # Print the loaded results
    
    if results is None or 'status' in results and results['status'] == 'processing':
        return ["Data is currently being processed."] * 10

    if 'top_buy_pair' not in results or 'top_short_pair' not in results:
        print(f"Missing top pairs in precomputed results for {ticker}: {results}")
        return ["Data not available or processing not yet complete. Please wait..."] * 10

    top_buy_pair = results['top_buy_pair']
    top_short_pair = results['top_short_pair']

    print("Top Buy Pair:", top_buy_pair)
    print("Top Short Pair:", top_short_pair)

    # Use the cached data instead of fetching it again
    df, _, _ = get_data(ticker, MAX_SMA_DAY)

    try:
        sma1_buy_leader = df.iloc[:, df.columns.get_loc(f'SMA_{top_buy_pair[0]}')]
        sma2_buy_leader = df.iloc[:, df.columns.get_loc(f'SMA_{top_buy_pair[1]}')]
        buy_signals_leader = sma1_buy_leader > sma2_buy_leader
        buy_returns_leader = df['Close'].pct_change().where(buy_signals_leader.shift(1, fill_value=False), 0)

        sma1_short_leader = df.iloc[:, df.columns.get_loc(f'SMA_{top_short_pair[0]}')]
        sma2_short_leader = df.iloc[:, df.columns.get_loc(f'SMA_{top_short_pair[1]}')]
        short_signals_leader = sma1_short_leader > sma2_short_leader
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

    df, sma_combinations, _ = get_data(ticker, MAX_SMA_DAY)
    if df is None or df.empty:
        return go.Figure(), '', '', '', '', '', '', '', ''

    start_date = df.index.min().strftime('%Y-%m-%d')

    sma1_buy = df[f'SMA_{sma_day_1}']
    sma2_buy = df[f'SMA_{sma_day_2}']
    buy_signals = sma1_buy > sma2_buy

    sma1_short = df[f'SMA_{sma_day_3}']
    sma2_short = df[f'SMA_{sma_day_4}']
    short_signals = sma1_short > sma2_short

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

# Run the app
if __name__ == '__main__':
    app.run_server(debug=True)