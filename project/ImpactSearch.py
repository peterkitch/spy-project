import os
import pandas as pd
import numpy as np
from scipy import stats
import logging
import dash
from dash import dcc, html, Input, Output, State
import dash_bootstrap_components as dbc
from functools import lru_cache
import plotly.graph_objects as go
import yfinance as yf
import pytz
import time
import threading
from dash.dependencies import Input, Output, State

# Initialize a logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)

file_handler = logging.FileHandler('secondary_ticker_analysis.log')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)
logger.propagate = False

# This script assumes:
# 1) You have a Dash app running elsewhere or integrated with this script.
# 2) You have access to precomputed primary ticker results (like ^GSPC) including their signals.
# 3) The user can specify a primary ticker and a list of secondary tickers via the Dash interface.
# 4) Once processed, a .xlsx file with the aggregated metrics is created in the project folder.

# NOTE: This script focuses on the single function logic to produce .xlsx results for secondary tickers.
# You would integrate this logic with your Dash callbacks. For demonstration, a placeholder Dash layout is included.

# ---------- Helper Functions ----------

def normalize_ticker(ticker):
    return ticker.strip().upper() if ticker else ticker

@lru_cache(maxsize=None)
def fetch_data(ticker):
    """Fetch data for a given ticker using yfinance."""
    if not ticker or not ticker.strip():
        return pd.DataFrame()
    ticker = normalize_ticker(ticker)
    
    try:
        df = yf.download(ticker, period='max', interval='1d', progress=False, auto_adjust=False)
        if df.empty:
            logger.warning(f"No data for {ticker}")
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index).tz_localize(None)

        # Standardize to 'Close' only
        if 'Adj Close' in df.columns:
            price_data = df['Adj Close']
        elif 'Close' in df.columns:
            price_data = df['Close']
        else:
            logger.error(f"No Close/Adj Close data found for {ticker}.")
            return pd.DataFrame()
        
        df = pd.DataFrame({'Close': price_data})
        # Optionally handle adding today's data if desired (omitted for brevity)
        return df
    except Exception as e:
        logger.error(f"Error fetching data for {ticker}: {str(e)}")
        return pd.DataFrame()

def calculate_secondary_metrics(primary_signals, primary_dates, secondary_df):
    """
    Given primary signals (a list of 'Buy', 'Short', or 'None'),
    primary_dates (DatetimeIndex), and a secondary DataFrame with 'Close',
    calculate the performance metrics of following the primary signals on the secondary asset.
    """
    # Align signals and secondary data
    common_dates = primary_dates.intersection(secondary_df.index)
    if len(common_dates) < 2:
        return None  # Insufficient overlap

    signals = pd.Series(primary_signals, index=primary_dates).loc[common_dates]
    prices = secondary_df['Close'].loc[common_dates]

    # Ensure alignment
    signals = signals.reindex(common_dates).fillna('None')
    prices = prices.reindex(common_dates).ffill()

    daily_returns = prices.pct_change().fillna(0)

    # Calculate captures
    buy_mask = signals == 'Buy'
    short_mask = signals == 'Short'
    trigger_days = (buy_mask | short_mask).sum()

    if trigger_days == 0:
        return None

    daily_captures = pd.Series(0.0, index=signals.index)
    daily_captures[buy_mask] = daily_returns[buy_mask] * 100
    daily_captures[short_mask] = -daily_returns[short_mask] * 100

    cumulative_captures = daily_captures.cumsum()
    signal_captures = daily_captures[buy_mask | short_mask]

    wins = (signal_captures > 0).sum()
    losses = trigger_days - wins
    win_ratio = (wins / trigger_days * 100) if trigger_days > 0 else 0.0
    avg_daily_capture = signal_captures.mean() if trigger_days > 0 else 0.0
    total_capture = cumulative_captures.iloc[-1] if not cumulative_captures.empty else 0.0
    std_dev = signal_captures.std() if trigger_days > 1 else 0.0

    # Sharpe ratio calculation (assuming 5% annual risk-free rate)
    risk_free_rate = 5.0
    if trigger_days > 0 and std_dev != 0:
        annualized_return = avg_daily_capture * 252
        annualized_std = std_dev * np.sqrt(252)
        sharpe_ratio = (annualized_return - risk_free_rate) / annualized_std
    else:
        sharpe_ratio = 0.0

    # Statistical significance
    if trigger_days > 1 and std_dev > 0:
        t_statistic = avg_daily_capture / (std_dev / np.sqrt(trigger_days))
        p_value = 2 * (1 - stats.t.cdf(abs(t_statistic), df=trigger_days - 1))
    else:
        t_statistic = None
        p_value = None

    significant_90 = 'Yes' if p_value is not None and p_value < 0.10 else 'No'
    significant_95 = 'Yes' if p_value is not None and p_value < 0.05 else 'No'
    significant_99 = 'Yes' if p_value is not None and p_value < 0.01 else 'No'

    return {
        'Trigger Days': int(trigger_days),
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
    """Export the results to an Excel file."""
    df = pd.DataFrame(metrics_list)
    if 'Sharpe Ratio' in df.columns:
        df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True)
    df.to_excel(output_filename, index=False)
    logger.info(f"Results exported to {output_filename}")

def process_secondary_tickers(primary_ticker, secondary_tickers, primary_signals, primary_dates):
    """
    Given:
    - primary_ticker (str): The primary ticker symbol used for signals.
    - secondary_tickers (list of str): A list of secondary tickers to process.
    - primary_signals (list of str): Signals from the primary ticker ('Buy', 'Short', 'None').
    - primary_dates (DatetimeIndex): The dates for which we have signals.

    This function:
    1. Fetches each secondary ticker's data.
    2. Calculates metrics following the primary ticker's signals.
    3. Stores the results in a .xlsx file named "<primary_ticker>_secondary_analysis.xlsx".
    """

    metrics_list = []
    for sec_ticker in secondary_tickers:
        sec_df = fetch_data(sec_ticker)
        if sec_df.empty:
            logger.warning(f"No data for secondary ticker {sec_ticker}, skipping.")
            continue
        result = calculate_secondary_metrics(primary_signals, primary_dates, sec_df)
        if result is not None:
            result['Primary Ticker'] = primary_ticker
            result['Secondary Ticker'] = sec_ticker
            metrics_list.append(result)
        else:
            logger.info(f"No valid triggers or insufficient overlap for {sec_ticker}, skipping.")

    if metrics_list:
        output_filename = f"{primary_ticker}_secondary_analysis.xlsx"
        export_results_to_excel(output_filename, metrics_list)
    else:
        logger.info("No valid results to export.")

# ---------- Dash App Integration (Example) ----------

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])

app.layout = dbc.Container([
    html.H1("Secondary Ticker Analysis", style={'color': '#80ff00'}),
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("Primary Ticker"),
                dbc.CardBody([
                    dbc.Input(id='primary-ticker-input', placeholder='Enter a primary ticker (e.g. ^GSPC)', type='text')
                ])
            ], className='mb-3')
        ], width=6),
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("Secondary Tickers"),
                dbc.CardBody([
                    dbc.Textarea(
                        id='secondary-tickers-input',
                        placeholder='Enter secondary tickers separated by commas (e.g. AAPL, MSFT, AMZN)',
                        style={'height': '100px'}
                    ),
                    html.Br(),
                    dbc.Button("Process", id='process-button', color='primary')
                ])
            ], className='mb-3')
        ], width=6)
    ]),
    html.Div(id='process-status', style={'color': '#80ff00', 'marginTop': '20px'})
], fluid=True)

@app.callback(
    Output('process-status', 'children'),
    [Input('process-button', 'n_clicks')],
    [State('primary-ticker-input', 'value'),
     State('secondary-tickers-input', 'value')]
)
def run_analysis(n_clicks, primary_ticker, secondary_tickers_input):
    if n_clicks is None or not primary_ticker or not secondary_tickers_input:
        raise dash.exceptions.PreventUpdate

    primary_ticker = normalize_ticker(primary_ticker)
    if not primary_ticker:
        return "Please enter a valid primary ticker."

    sec_tickers = [t.strip().upper() for t in secondary_tickers_input.split(',') if t.strip()]
    if not sec_tickers:
        return "Please enter at least one secondary ticker."

    # For demonstration:
    # Here you would load or have precomputed `primary_signals` and `primary_dates`.
    # We'll assume we have a placeholder primary_signals (all "Buy") and primary_dates.

    # In a real scenario, you'd integrate with your existing logic that generates
    # these signals from the primary ticker. For now, let's simulate:
    df = fetch_data(primary_ticker)
    if df.empty:
        return f"No data for {primary_ticker}, cannot proceed."

    primary_dates = df.index
    # Dummy signals: alternate Buy/Short every day, just as an example
    # In reality, you'd load these from your precomputed results.
    primary_signals = []
    for i in range(len(primary_dates)):
        if i % 2 == 0:
            primary_signals.append('Buy')
        else:
            primary_signals.append('Short')

    # Process secondary tickers
    process_secondary_tickers(primary_ticker, sec_tickers, primary_signals, primary_dates)

    return f"Processing complete. Check {primary_ticker}_secondary_analysis.xlsx for results."

if __name__ == "__main__":
    # Run the Dash app
    app.run_server(debug=True)
