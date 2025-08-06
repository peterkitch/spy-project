import os
import pandas as pd
import numpy as np
from scipy import stats
import logging
import dash
from dash import dcc, html, Input, Output, State
import dash_bootstrap_components as dbc
import yfinance as yf
from tqdm import tqdm

# Remove all handlers from the root logger
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # <-- CHANGED from INFO to DEBUG

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)  # Keep console at INFO to avoid too many prints
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)

file_handler = logging.FileHandler('logs/impactsearch.log', mode='w')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)

logger.handlers.clear()
logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.propagate = False

# Constants
MAX_SMA_DAY = 114  # Set fixed window for SMA calculations

def normalize_ticker(ticker):
    return ticker.strip().upper() if ticker else ticker

def fetch_data(ticker):
    if not ticker or not ticker.strip():
        return pd.DataFrame()
    ticker = normalize_ticker(ticker)
    try:
        logger.info(f"Fetching data for {ticker}...")
        df = yf.download(ticker, period='max', interval='1d', progress=False, auto_adjust=False)
        if df.empty:
            logger.warning(f"No data returned for {ticker}.")
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index).tz_localize(None)

        if 'Adj Close' in df.columns:
            df = df[['Adj Close']]
            df.columns = ['Close']
        elif 'Close' in df.columns:
            df = df[['Close']]
        else:
            logger.error(f"No Close/Adj Close data found for {ticker}, aborting this ticker.")
            return pd.DataFrame()

        logger.info(f"Successfully fetched {len(df)} days of data for {ticker}.")
        return df
    except Exception as e:
        logger.error(f"Error fetching data for {ticker}: {str(e)}")
        return pd.DataFrame()

def calculate_metrics_from_signals(primary_signals, primary_dates, secondary_df):
    logger.debug("Calculating final metrics from generated signals...")
    
    # Extra prints to debug alignment issues
    logger.debug(f"Initial primary_signals length: {len(primary_signals)}")
    logger.debug(f"Initial primary_dates range: {primary_dates[0]} to {primary_dates[-1]} (len={len(primary_dates)})")
    logger.debug(f"secondary_df index range: {secondary_df.index[0]} to {secondary_df.index[-1]} (len={len(secondary_df)})")
    
    # Create signals series with original dates (no reindexing yet)
    signals = pd.Series(primary_signals, index=primary_dates)

    logger.debug(f"Signals head (unshifted):\n{signals.head(5)}")
    logger.debug(f"Signals tail (unshifted):\n{signals.tail(5)}")
    
    # Get common dates between signals and prices
    common_dates = sorted(set(primary_dates) & set(secondary_df.index))
    logger.debug(f"Number of common dates between signals & secondary: {len(common_dates)}")

    if len(common_dates) < 2:  # Only need 2 days minimum for valid calculations
        logger.debug("Insufficient overlapping dates for metrics calculation.")
        return None
        
    # Use all valid dates - MAX_SMA_DAY is just for SMA calculations
    valid_dates = common_dates
    
    # Now reindex both series to valid dates
    signals = signals.reindex(valid_dates).fillna('None')
    prices = secondary_df['Close'].reindex(valid_dates)
    
    logger.debug(f"Signals head after reindex:\n{signals.head(5)}")
    logger.debug(f"Prices head after reindex:\n{prices.head(5)}")
    
    # Returns are calculated after date alignment but before signal processing
    daily_returns = prices.pct_change()
    
    logger.debug(f"Daily returns head (pre-shift alignment):\n{daily_returns.head(5)}")
    
    # If we need to capture on the same day a signal appears (no one-day delay),
    # remove the shift and keep all dates from the start.
    logger.debug(f"Signals head (NO SHIFT):\n{signals.head(5)}")

    valid_dates = signals.index  # Keep all valid dates without dropping the first
    signals = signals.loc[valid_dates]
    daily_returns = daily_returns.loc[valid_dates]

    logger.debug(f"Signals index after dropping first date: {signals.index[0]} ... {signals.index[-1]}")
    logger.debug(f"Daily returns index after dropping first date: {daily_returns.index[0]} ... {daily_returns.index[-1]}")
    
    # Clean signals and create masks
    signals = signals.fillna('None').str.strip()  # Ensure no NaN values
    buy_mask = signals.eq('Buy')
    short_mask = signals.eq('Short')
    trigger_mask = buy_mask | short_mask
    trigger_days = int(trigger_mask.sum())

    logger.debug(f"Final signals distribution:\n{signals.value_counts()}")
    logger.debug(f"Number of trigger days: {trigger_days}")

    if trigger_days == 0:
        logger.debug("No trigger days found, no metrics to report.")
        return None

    # Calculate captures for trigger days
    daily_captures = pd.Series(0.0, index=signals.index)
    daily_captures.loc[buy_mask] = daily_returns.loc[buy_mask] * 100
    daily_captures.loc[short_mask] = -daily_returns.loc[short_mask] * 100

    # Get captures only for trigger days
    signal_captures = daily_captures[trigger_mask]

    logger.debug(f"Sample of signal_captures:\n{signal_captures.head(10)}\n...\n{signal_captures.tail(10)}")

    # Calculate basic metrics
    wins = (signal_captures > 0).sum()
    losses = trigger_days - wins
    win_ratio = (wins / trigger_days * 100)
    avg_daily_capture = signal_captures.mean()
    total_capture = signal_captures.sum()
    
    logger.debug(f"wins={wins}, losses={losses}, win_ratio={win_ratio:.2f}%")
    logger.debug(f"avg_daily_capture={avg_daily_capture:.4f}%, total_capture={total_capture:.4f}%")
    
    # Calculate standard deviation using ddof=1 for sample standard deviation
    if trigger_days > 1:
        std_dev = signal_captures.std(ddof=1)
        
        # Calculate Sharpe ratio
        risk_free_rate = 5.0  # 5% annual rate
        annualized_return = avg_daily_capture * 252
        annualized_std = std_dev * np.sqrt(252)
        sharpe_ratio = (annualized_return - risk_free_rate) / annualized_std if annualized_std != 0 else 0.0
        
        # Calculate t-statistic and p-value
        t_statistic = avg_daily_capture / (std_dev / np.sqrt(trigger_days))
        p_value = 2 * (1 - stats.t.cdf(abs(t_statistic), df=trigger_days - 1))
    else:
        std_dev = 0.0
        sharpe_ratio = 0.0
        t_statistic = None
        p_value = None

    # Determine significance levels
    significant_90 = 'Yes' if p_value is not None and p_value < 0.10 else 'No'
    significant_95 = 'Yes' if p_value is not None and p_value < 0.05 else 'No'
    significant_99 = 'Yes' if p_value is not None and p_value < 0.01 else 'No'

    logger.debug(
        f"Metrics:\n"
        f"  Trigger Days={trigger_days}\n"
        f"  Wins={wins}\n"
        f"  Losses={losses}\n"
        f"  Win Ratio={win_ratio:.2f}%\n"
        f"  StdDev={std_dev:.4f}%\n"
        f"  Sharpe Ratio={sharpe_ratio:.2f}\n"
        f"  Avg Daily Capture={avg_daily_capture:.4f}%\n"
        f"  Total Capture={total_capture:.4f}%\n"
        f"  t-Statistic={t_statistic if t_statistic is not None else 'N/A'}\n"
        f"  p-Value={p_value if p_value is not None else 'N/A'}"
    )

    return {
        'Trigger Days': trigger_days,
        'Wins': int(wins),
        'Losses': int(losses),
        'Win Ratio (%)': round(win_ratio, 2),
        'Std Dev (%)': round(std_dev, 4),
        'Sharpe Ratio': round(sharpe_ratio, 2),
        'Avg Daily Capture (%)': round(avg_daily_capture, 4),
        'Total Capture (%)': round(total_capture, 4),
        't-Statistic': round(t_statistic, 4) if t_statistic is not None else 'N/A',
        'p-Value': round(p_value, 4) if p_value is not None else 'N/A',
        'Significant 90%': significant_90,
        'Significant 95%': significant_95,
        'Significant 99%': significant_99
    }

def export_results_to_excel(output_filename, metrics_list):
    logger.info(f"Exporting results to {output_filename}...")

    # Define your desired column order
    desired_order = [
        'Primary Ticker',
        'Trigger Days',
        'Wins',
        'Losses',
        'Win Ratio (%)',
        'Std Dev (%)',
        'Sharpe Ratio',
        't-Statistic',
        'p-Value',
        'Significant 90%',
        'Significant 95%',
        'Significant 99%',
        'Avg Daily Capture (%)',
        'Total Capture (%)'
    ]

    if os.path.exists(output_filename):
        existing_df = pd.read_excel(output_filename)
        new_df = pd.DataFrame(metrics_list)
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)

        # Optionally sort by Sharpe Ratio
        if 'Sharpe Ratio' in combined_df.columns:
            combined_df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True)

        # Reorder columns if they exist
        for col in desired_order:
            if col not in combined_df.columns:
                combined_df[col] = np.nan
        combined_df = combined_df[[col for col in desired_order if col in combined_df.columns]]

        combined_df.to_excel(output_filename, index=False)
    else:
        df = pd.DataFrame(metrics_list)

        # Optionally sort by Sharpe Ratio
        if 'Sharpe Ratio' in df.columns:
            df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True)

        # Reorder columns if they exist
        for col in desired_order:
            if col not in df.columns:
                df[col] = np.nan
        df = df[[col for col in desired_order if col in df.columns]]

        df.to_excel(output_filename, index=False)

    logger.info("Results successfully exported.")

def process_primary_tickers(secondary_ticker, primary_tickers):
    secondary_ticker = normalize_ticker(secondary_ticker)
    sec_df = fetch_data(secondary_ticker)
    if sec_df.empty:
        logger.error(f"No data for secondary ticker {secondary_ticker}, cannot proceed.")
        return []
        
    # Ensure proper data alignment from the start
    sec_df = sec_df.sort_index()

    metrics_list = []
    MAX_SMA_DAY = 114

    logger.info(f"Starting analysis for Secondary Ticker: {secondary_ticker}")
    logger.info("Verifying logic alignment with main script and ensuring correct computation steps...")
    logger.info("We will compute SMAs, generate signals, identify top pairs daily, and then derive the final metrics.")

    for prim_ticker in tqdm(primary_tickers, desc="Processing Primary Tickers", unit="ticker"):
        prim_ticker = normalize_ticker(prim_ticker)
        logger.info(f"Processing {prim_ticker}...")
        df = fetch_data(prim_ticker)
        if df.empty:
            logger.warning(f"No data for primary ticker {prim_ticker}, skipping.")
            continue

        close_values = df['Close'].values
        num_days = len(df)
        if num_days < 2:
            logger.warning(f"Insufficient days of data for {prim_ticker}, skipping.")
            continue

        logger.info("Computing SMAs...")
        cumsum = np.cumsum(np.insert(close_values, 0, 0))
        sma_matrix = np.empty((num_days, MAX_SMA_DAY), dtype=float)
        sma_matrix.fill(np.nan)
        for i in range(1, MAX_SMA_DAY + 1):
            valid_indices = np.arange(i-1, num_days)
            sma_matrix[valid_indices, i-1] = (cumsum[valid_indices+1] - cumsum[valid_indices+1 - i]) / i

        # Use pct_change to get returns aligned properly
        logger.info("Computing returns using pct_change() to avoid broadcast issues...")
        returns = df['Close'].pct_change().fillna(0).values * 100

        # Generate all pairs
        i_array = np.arange(1, MAX_SMA_DAY+1)
        j_array = np.arange(1, MAX_SMA_DAY+1)
        pairs = np.array([(i, j) for i in i_array for j in j_array if i != j], dtype=int)
        i_indices = pairs[:,0] - 1
        j_indices = pairs[:,1] - 1

        logger.info("Generating signals from SMA comparisons...")
        sma_i = sma_matrix[:, i_indices]
        sma_j = sma_matrix[:, j_indices]

        buy_signals = (sma_i > sma_j)
        short_signals = (sma_i < sma_j)

        signals = np.full((num_days, len(pairs)), 0)
        valid_sma = np.isfinite(sma_i) & np.isfinite(sma_j)
        signals[1:][valid_sma[:-1]] = np.where(buy_signals[:-1][valid_sma[:-1]], 1, 
                                            np.where(short_signals[:-1][valid_sma[:-1]], -1, 0))
            
        logger.info("Computing buy/short captures and cumulative sums...")
        buy_captures = np.where(signals == 1, returns[:,None], 0)
        short_captures = np.where(signals == -1, returns[:,None]*(-1), 0)

        buy_cumulative = np.nancumsum(buy_captures, axis=0)
        short_cumulative = np.nancumsum(short_captures, axis=0)

        logger.info("Selecting daily top pairs based on cumulative captures...")
        daily_top_buy_pairs = {}
        daily_top_short_pairs = {}
        for idx, date in enumerate(df.index):
            buy_day = buy_cumulative[idx]
            short_day = short_cumulative[idx]

            # Reverse priority tie-breaking logic
            max_buy_idx = len(buy_day) - 1 - np.argmax(buy_day[::-1])
            max_short_idx = len(short_day) - 1 - np.argmax(short_day[::-1])

            # Only assign valid signals if we have enough data
            if np.isfinite(buy_day[max_buy_idx]):
                top_buy_pair = (pairs[max_buy_idx,0], pairs[max_buy_idx,1])
                buy_value = buy_day[max_buy_idx]
            else:
                top_buy_pair = (1,2)
                buy_value = 0.0

            if np.isfinite(short_day[max_short_idx]):
                top_short_pair = (pairs[max_short_idx,0], pairs[max_short_idx,1])
                short_value = short_day[max_short_idx]
            else:
                top_short_pair = (1,2)
                short_value = 0.0

            daily_top_buy_pairs[date] = (top_buy_pair, buy_value)
            daily_top_short_pairs[date] = (top_short_pair, short_value)

        logger.info("Deriving primary signals from previous day's top pairs...")
        primary_dates = df.index
        primary_signals = []
        previous_date = None


        for date in primary_dates:
            if previous_date is None:
                primary_signals.append('None')
                previous_date = date
                continue

            buy_pair, buy_val = daily_top_buy_pairs.get(previous_date, ((1,2),0.0))
            short_pair, short_val = daily_top_short_pairs.get(previous_date, ((1,2),0.0))

            # Get previous day's SMA values
            sma1_buy = sma_matrix[df.index.get_loc(previous_date), buy_pair[0]-1]
            sma2_buy = sma_matrix[df.index.get_loc(previous_date), buy_pair[1]-1]
            sma1_short = sma_matrix[df.index.get_loc(previous_date), short_pair[0]-1]
            sma2_short = sma_matrix[df.index.get_loc(previous_date), short_pair[1]-1]

            buy_signal = sma1_buy > sma2_buy
            short_signal = sma1_short < sma2_short

            if buy_signal and short_signal:
                current_signal = 'Buy' if buy_val > short_val else 'Short'
            elif buy_signal:
                current_signal = 'Buy'
            elif short_signal:
                current_signal = 'Short'
            else:
                current_signal = 'None'

            primary_signals.append(current_signal)
            previous_date = date

        logger.info("Calculating final metrics for this primary ticker...")
        logger.info(f"Signal distribution before metrics calculation:")
        signal_counts = pd.Series(primary_signals).value_counts()
        logger.info(f"Buy signals: {signal_counts.get('Buy', 0)}")
        logger.info(f"Short signals: {signal_counts.get('Short', 0)}")
        logger.info(f"None signals: {signal_counts.get('None', 0)}")
        result = calculate_metrics_from_signals(primary_signals, primary_dates, sec_df)
        if result is not None:
            result['Primary Ticker'] = prim_ticker
            result['Secondary Ticker'] = secondary_ticker
            metrics_list.append(result)
        else:
            logger.info(f"No valid triggers or insufficient overlap for {prim_ticker}, skipping.")

        logger.info(f"Completed processing for {prim_ticker}.")

    return metrics_list

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])

app.layout = dbc.Container([
    html.H1("Impact Analysis Tool", style={'color': '#80ff00'}),
    html.P("Use this tool to analyze the impact of various primary tickers against a single secondary ticker. "
           "Enter one or multiple primary tickers separated by commas, and a single secondary ticker. Then click 'Process'."),
    html.P("If you have a large number of primary tickers, processing will take time. We have enabled vectorization, "
           "logging, and a TQDM console progress bar to provide insight. Check the console and logs/impactsearch.log for details."),
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("Primary Tickers"),
                dbc.CardBody([
                    html.P("Example: AAPL, MSFT, AMZN"),
                    dbc.Textarea(
                        id='primary-tickers-input',
                        placeholder='Enter primary tickers separated by commas...',
                        style={'height': '100px'}
                    )
                ])
            ], className='mb-3')
        ], width=6),
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("Secondary Ticker"),
                dbc.CardBody([
                    html.P("Example: ^GSPC"),
                    dbc.Input(id='secondary-ticker-input', placeholder='Enter a secondary ticker', type='text'),
                    html.Br(),
                    dbc.Button("Process", id='process-button', color='primary', style={'width': '100%'})
                ])
            ], className='mb-3')
        ], width=6)
    ]),
    html.Div(id='process-status', style={'color': '#80ff00', 'marginTop': '20px'}),
    dbc.Progress(id='progress-bar', value=0, striped=True, animated=True, style={'marginTop': '20px', 'height': '30px'}, color='success'),
    html.P("After processing, results are exported to an Excel file named after the secondary ticker. "
           "You can re-run analysis with different sets of primary tickers to append results.")
], fluid=True)

@app.callback(
    [Output('process-status', 'children'),
     Output('progress-bar', 'value'),
     Output('progress-bar', 'style')],
    [Input('process-button', 'n_clicks')],
    [State('secondary-ticker-input', 'value'),
     State('primary-tickers-input', 'value')]
)
def run_analysis(n_clicks, secondary_ticker, primary_tickers_input):
    """
    NOTE:
      This approach simulates incremental progress updates within a single callback. 
      However, due to Dash's single-round callback execution, the UI will only fully refresh 
      once the entire process finishes. For truly "live" updates, you'll need 
      a multi-callback approach with dcc.Interval or background tasks.
    """
    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    if not secondary_ticker or not primary_tickers_input:
        return "Please enter a secondary ticker and at least one primary ticker.", 0, {'marginTop': '20px', 'height': '30px'}

    secondary_ticker = normalize_ticker(secondary_ticker)
    primary_tickers = [t.strip().upper() for t in primary_tickers_input.split(',') if t.strip()]

    if not primary_tickers:
        return "Please enter at least one valid primary ticker.", 0, {'marginTop': '20px', 'height': '30px'}

    logger.info("----- STARTING ANALYSIS -----")
    logger.info(f"Secondary Ticker: {secondary_ticker}")
    logger.info(f"Primary Tickers: {primary_tickers}")
    logger.info("Processing started. Please wait...")

    total_tickers = len(primary_tickers)
    progress_value = 0
    message = "Processing in progress..."

    # Manually loop through each ticker to simulate small step updates
    processed_metrics = []
    for i, ticker in enumerate(primary_tickers, start=1):
        # Process each ticker individually
        single_metrics_list = process_primary_tickers(secondary_ticker, [ticker])

        if single_metrics_list:
            processed_metrics.extend(single_metrics_list)

        # Compute incremental progress (not fully "live" in a single callback)
        progress_value = int((i / total_tickers) * 100)
        message = f"Processed {i} of {total_tickers} tickers ({progress_value}%)..."

        # OPTIONAL: Sleep or pass here
        # time.sleep(0.2)

    # Once done, export if we have any results
    if processed_metrics:
        output_filename = f"output/analysis/{secondary_ticker}_analysis.xlsx"
        export_results_to_excel(output_filename, processed_metrics)
        message = f"Processing complete. Check {output_filename} for results."
        progress_value = 100
    else:
        message = "No valid results to export."
        progress_value = 100

    logger.info("----- ANALYSIS COMPLETE -----")
    return message, progress_value, {'marginTop': '20px', 'height': '30px'}

if __name__ == "__main__":
    # Clean up old log files
    log_files = ['logs/analysis.log', 'logs/debug.log', 'logs/impactsearch.log']
    for file in log_files:
        if os.path.exists(file):
            try:
                os.remove(file)
            except:
                pass
                
    app.run_server(debug=True, port=8051)