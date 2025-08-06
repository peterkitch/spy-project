# onepass.py

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
logger.setLevel(logging.DEBUG)  # Keep debug logging

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)

file_handler = logging.FileHandler('logs/onepass.log', mode='w')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)

logger.handlers.clear()
logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.propagate = False

# Constants
MAX_SMA_DAY = 114  # Same logic as impactsearch.py

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

def calculate_metrics_from_signals(primary_signals, primary_dates, df_for_returns):
    """
    Matches the logic from impactsearch.py but uses the same DataFrame (df_for_returns)
    for both signals and return calculations.
    """
    logger.debug("Calculating final metrics from generated signals...")
    logger.debug(f"Initial primary_signals length: {len(primary_signals)}")
    logger.debug(f"Initial primary_dates range: {primary_dates[0]} to {primary_dates[-1]} (len={len(primary_dates)})")
    logger.debug(f"df_for_returns index range: {df_for_returns.index[0]} to {df_for_returns.index[-1]} (len={len(df_for_returns)})")

    signals = pd.Series(primary_signals, index=primary_dates)

    # Determine overlapping dates
    common_dates = sorted(set(primary_dates) & set(df_for_returns.index))
    logger.debug(f"Number of common dates between signals & data: {len(common_dates)}")
    if len(common_dates) < 2:
        logger.debug("Insufficient overlapping dates for metrics calculation.")
        return None

    signals = signals.reindex(common_dates).fillna('None')
    prices = df_for_returns['Close'].reindex(common_dates)

    daily_returns = prices.pct_change()
    signals = signals.fillna('None').str.strip()

    buy_mask = signals.eq('Buy')
    short_mask = signals.eq('Short')
    trigger_mask = buy_mask | short_mask
    trigger_days = int(trigger_mask.sum())

    if trigger_days == 0:
        logger.debug("No trigger days found, no metrics to report.")
        return None

    daily_captures = pd.Series(0.0, index=signals.index)
    daily_captures.loc[buy_mask] = daily_returns.loc[buy_mask] * 100
    daily_captures.loc[short_mask] = -daily_returns.loc[short_mask] * 100

    signal_captures = daily_captures[trigger_mask]

    wins = (signal_captures > 0).sum()
    losses = trigger_days - wins
    win_ratio = (wins / trigger_days * 100) if trigger_days else 0.0
    avg_daily_capture = signal_captures.mean() if trigger_days else 0.0
    total_capture = signal_captures.sum() if trigger_days else 0.0

    if trigger_days > 1:
        std_dev = signal_captures.std(ddof=1)
        risk_free_rate = 5.0
        annualized_return = avg_daily_capture * 252
        annualized_std = std_dev * np.sqrt(252)
        sharpe_ratio = (annualized_return - risk_free_rate) / annualized_std if annualized_std != 0 else 0.0

        t_statistic = avg_daily_capture / (std_dev / np.sqrt(trigger_days)) if std_dev != 0 else None
        p_value = (2 * (1 - stats.t.cdf(abs(t_statistic), df=trigger_days - 1))) if t_statistic else None
    else:
        std_dev = 0.0
        sharpe_ratio = 0.0
        t_statistic = None
        p_value = None

    significant_90 = 'Yes' if p_value and p_value < 0.10 else 'No'
    significant_95 = 'Yes' if p_value and p_value < 0.05 else 'No'
    significant_99 = 'Yes' if p_value and p_value < 0.01 else 'No'

    return {
        'Trigger Days': trigger_days,
        'Wins': int(wins),
        'Losses': int(losses),
        'Win Ratio (%)': round(win_ratio, 2),
        'Std Dev (%)': round(std_dev, 4),
        'Sharpe Ratio': round(sharpe_ratio, 2),
        'Avg Daily Capture (%)': round(avg_daily_capture, 4),
        'Total Capture (%)': round(total_capture, 4),
        't-Statistic': round(t_statistic, 4) if t_statistic else 'N/A',
        'p-Value': round(p_value, 4) if p_value else 'N/A',
        'Significant 90%': significant_90,
        'Significant 95%': significant_95,
        'Significant 99%': significant_99
    }

def export_results_to_excel(output_filename, metrics_list):
    logger.info(f"Exporting results to {output_filename}...")

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

        if 'Sharpe Ratio' in combined_df.columns:
            combined_df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True)

        for col in desired_order:
            if col not in combined_df.columns:
                combined_df[col] = np.nan
        combined_df = combined_df[[col for col in desired_order if col in combined_df.columns]]

        combined_df.to_excel(output_filename, index=False)
    else:
        df = pd.DataFrame(metrics_list)

        if 'Sharpe Ratio' in df.columns:
            df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True)

        for col in desired_order:
            if col not in df.columns:
                df[col] = np.nan
        df = df[[col for col in desired_order if col in df.columns]]

        df.to_excel(output_filename, index=False)

    logger.info("Results successfully exported.")

def process_onepass_tickers(tickers_list):
    """
    One-pass logic. 
    For each ticker in 'tickers_list':
      1) fetch data
      2) run full SMA-based logic
      3) generate signals
      4) measure performance using the same data as returns
    Return a list of metric dictionaries.
    """
    metrics_list = []
    for ticker in tqdm(tickers_list, desc="Processing One-Pass Tickers", unit="ticker"):
        ticker = normalize_ticker(ticker)
        logger.info(f"Processing {ticker}...")
        df = fetch_data(ticker)
        if df.empty:
            logger.warning(f"No data for ticker {ticker}, skipping.")
            continue

        close_values = df['Close'].values
        num_days = len(df)
        if num_days < 2:
            logger.warning(f"Insufficient days of data for {ticker}, skipping.")
            continue

        logger.info("Computing SMAs...")
        cumsum = np.cumsum(np.insert(close_values, 0, 0))
        sma_matrix = np.empty((num_days, MAX_SMA_DAY), dtype=float)
        sma_matrix.fill(np.nan)
        for i in range(1, MAX_SMA_DAY + 1):
            valid_indices = np.arange(i-1, num_days)
            sma_matrix[valid_indices, i-1] = (cumsum[valid_indices+1] - cumsum[valid_indices+1 - i]) / i

        logger.info("Computing returns using pct_change() to avoid broadcast issues...")
        returns = df['Close'].pct_change().fillna(0).values * 100

        i_array = np.arange(1, MAX_SMA_DAY+1)
        j_array = np.arange(1, MAX_SMA_DAY+1)
        pairs = np.array([(a, b) for a in i_array for b in j_array if a != b], dtype=int)
        i_indices = pairs[:, 0] - 1
        j_indices = pairs[:, 1] - 1

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
        buy_captures = np.where(signals == 1, returns[:, None], 0)
        short_captures = np.where(signals == -1, returns[:, None] * (-1), 0)

        buy_cumulative = np.nancumsum(buy_captures, axis=0)
        short_cumulative = np.nancumsum(short_captures, axis=0)

        logger.info("Selecting daily top pairs based on cumulative captures...")
        daily_top_buy_pairs = {}
        daily_top_short_pairs = {}
        for idx_date, date in enumerate(df.index):
            buy_day = buy_cumulative[idx_date]
            short_day = short_cumulative[idx_date]

            max_buy_idx = len(buy_day) - 1 - np.argmax(buy_day[::-1])
            max_short_idx = len(short_day) - 1 - np.argmax(short_day[::-1])

            if np.isfinite(buy_day[max_buy_idx]):
                top_buy_pair = (pairs[max_buy_idx, 0], pairs[max_buy_idx, 1])
                buy_value = buy_day[max_buy_idx]
            else:
                top_buy_pair = (1, 2)
                buy_value = 0.0

            if np.isfinite(short_day[max_short_idx]):
                top_short_pair = (pairs[max_short_idx, 0], pairs[max_short_idx, 1])
                short_value = short_day[max_short_idx]
            else:
                top_short_pair = (1, 2)
                short_value = 0.0

            daily_top_buy_pairs[date] = (top_buy_pair, buy_value)
            daily_top_short_pairs[date] = (top_short_pair, short_value)

        logger.info("Deriving primary signals from previous day's top pairs...")
        primary_dates = df.index
        primary_signals = []
        prev_date = None

        for current_date in primary_dates:
            if prev_date is None:
                primary_signals.append('None')
                prev_date = current_date
                continue

            buy_pair, buy_val = daily_top_buy_pairs.get(prev_date, ((1,2), 0.0))
            short_pair, short_val = daily_top_short_pairs.get(prev_date, ((1,2), 0.0))

            sma1_buy = sma_matrix[df.index.get_loc(prev_date), buy_pair[0]-1]
            sma2_buy = sma_matrix[df.index.get_loc(prev_date), buy_pair[1]-1]
            sma1_short = sma_matrix[df.index.get_loc(prev_date), short_pair[0]-1]
            sma2_short = sma_matrix[df.index.get_loc(prev_date), short_pair[1]-1]

            buy_signal = sma1_buy > sma2_buy
            short_signal = sma1_short < sma2_short

            if buy_signal and short_signal:
                signal_of_day = 'Buy' if buy_val > short_val else 'Short'
            elif buy_signal:
                signal_of_day = 'Buy'
            elif short_signal:
                signal_of_day = 'Short'
            else:
                signal_of_day = 'None'

            primary_signals.append(signal_of_day)
            prev_date = current_date

        logger.info("Calculating final metrics for this ticker...")
        logger.info(f"Signal distribution before metrics calculation:")
        s_counts = pd.Series(primary_signals).value_counts()
        logger.info(f"Buy signals: {s_counts.get('Buy', 0)}")
        logger.info(f"Short signals: {s_counts.get('Short', 0)}")
        logger.info(f"None signals: {s_counts.get('None', 0)}")

        # Now measure performance using the same df for returns
        result = calculate_metrics_from_signals(primary_signals, primary_dates, df)
        if result is not None:
            result['Primary Ticker'] = ticker
            metrics_list.append(result)
        else:
            logger.info(f"No valid triggers for {ticker}, skipping metrics.")

        logger.info(f"Completed processing for {ticker}.")

    return metrics_list

##################
# DASH APP LAYOUT
##################

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])
app.layout = dbc.Container([
    html.H1("One-Pass Primary Analysis", style={'color': '#80ff00'}),
    html.P("Enter multiple primary tickers separated by commas, then click Process. "
           "Results will be exported to onepass.xlsx."),
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
                    ),
                    html.Br(),
                    dbc.Button("Process", id='process-button', color='primary', style={'width': '100%'})
                ])
            ], className='mb-3')
        ], width=12),
    ]),
    html.Div(id='process-status', style={'color': '#80ff00', 'marginTop': '20px'}),
    dbc.Progress(id='progress-bar', value=0, striped=True, animated=True,
                 style={'marginTop': '20px', 'height': '30px'}, color='success'),
], fluid=True)


##################
# DASH CALLBACK
##################

@app.callback(
    [Output('process-status', 'children'),
     Output('progress-bar', 'value'),
     Output('progress-bar', 'style')],
    [Input('process-button', 'n_clicks')],
    [State('primary-tickers-input', 'value')]
)
def run_onepass_analysis(n_clicks, primary_tickers_input):
    """
    Single-run "onepass" script. 
    Accept user input of multiple tickers, run the entire logic for each ticker, 
    then export results to onepass.xlsx. 
    """
    if not n_clicks:
        raise dash.exceptions.PreventUpdate

    if not primary_tickers_input:
        return "Please enter at least one primary ticker.", 0, {'marginTop': '20px', 'height': '30px'}

    tickers_list = [t.strip().upper() for t in primary_tickers_input.split(',') if t.strip()]
    if not tickers_list:
        return "Please enter valid ticker symbols.", 0, {'marginTop': '20px', 'height': '30px'}

    logger.info("----- STARTING ONE-PASS ANALYSIS -----")
    logger.info(f"Primary Tickers: {tickers_list}")
    logger.info("Processing started. Please wait...")

    total_tickers = len(tickers_list)
    progress_value = 0
    message = "Processing in progress..."

    processed_metrics = []
    # We'll do a manual loop to simulate incremental progress
    for i, tk in enumerate(tickers_list, start=1):
        single_result = process_onepass_tickers([tk])  # Processes exactly 1 ticker
        if single_result:
            processed_metrics.extend(single_result)

        progress_value = int((i / total_tickers) * 100)
        message = f"Processed {i} of {total_tickers} tickers ({progress_value}%)..."

    # Once done, export if we have any results
    if processed_metrics:
        out_file = "output/analysis/onepass.xlsx"
        export_results_to_excel(out_file, processed_metrics)
        message = f"Processing complete. Check {out_file} for results."
        progress_value = 100
    else:
        message = "No valid results to export."
        progress_value = 100

    logger.info("----- ONE-PASS ANALYSIS COMPLETE -----")
    return message, progress_value, {'marginTop': '20px', 'height': '30px'}

##################
# MAIN
##################

if __name__ == "__main__":
    # Optional: Clean up old logs if needed
    log_files = ['logs/analysis.log', 'logs/debug.log', 'logs/onepass.log']
    for file in log_files:
        if os.path.exists(file):
            try:
                os.remove(file)
            except:
                pass

    app.run_server(debug=True, port=8052)
