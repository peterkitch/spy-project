import yfinance as yf
import plotly.graph_objects as go
from dash import Dash, dcc, html
from dash.dependencies import Input, Output
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

# Fetch data
@lru_cache(maxsize=None)
def fetch_data():
    return yf.download('^GSPC', start='1927-12-30')

df = fetch_data()

# Set the maximum window size for SMAs
max_window_size = len(df)

# Calculate SMAs for window sizes 1 to the maximum window size
@lru_cache(maxsize=None)
def calculate_sma(window):
    column_name = f'SMA_{window}'
    return df['Close'].rolling(window=window).mean()

with ThreadPoolExecutor() as executor:
    sma_results = list(executor.map(calculate_sma, range(1, max_window_size + 1)))

sma_columns = {f'SMA_{i+1}': sma for i, sma in enumerate(sma_results)}

# Concatenate the SMA columns with the original DataFrame
df = pd.concat([df, pd.DataFrame(sma_columns)], axis=1)

# Initialize the Dash app
app = Dash(__name__)

# Define the app layout
app.layout = html.Div([
    dcc.Graph(id='chart'),
    html.Div([
        html.Label(f'Enter 1st SMA Day from 1 - {max_window_size}:'),
        dcc.Input(id='sma-input-1', type='number', value=200, min=1, max=max_window_size, step=1),
    ]),
    html.Div([
        html.Label(f'Enter 2nd SMA Day from 1 - {max_window_size}:'),
        dcc.Input(id='sma-input-2', type='number', value=50, min=1, max=max_window_size, step=1),
    ]),
    html.Div(id='total-capture-stats')
])

# Callback function to update the chart based on user input
@app.callback(
    [Output('chart', 'figure'),
     Output('total-capture-stats', 'children')],
    [Input('sma-input-1', 'value'),
     Input('sma-input-2', 'value')]
)
def update_chart(sma_day_1, sma_day_2):
    # Create the chart figure
    fig = go.Figure()

    # Add S&P 500 closing prices trace
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name='S&P 500 Close'))

    # Calculate and Add Buy and Short stats, if SMAs are valid
    if sma_day_1 is None or sma_day_2 is None or sma_day_1 < 1 or sma_day_1 > max_window_size or sma_day_2 < 1 or sma_day_2 > max_window_size:
        # Show a default trace if SMAs are not valid
        fig.add_trace(go.Scatter(x=df.index, y=[0] * len(df), mode='lines', name='Default'))
        total_capture_stats = ["No valid SMA days entered"]
    else:
        sma1 = df[f'SMA_{sma_day_1}'].values
        sma2 = df[f'SMA_{sma_day_2}'].values
        close_prices = df['Close'].values

    # Calculate continuous Buy and Short signals
        buy_signals = sma1 > sma2
        short_signals = sma1 < sma2

    # Calculate daily returns
        daily_returns = close_prices[1:] / close_prices[:-1] - 1

    # Calculate Buy and Short returns based on continuous signals
        buy_returns = np.where(buy_signals[:-1], daily_returns, 0)
        short_returns = np.where(short_signals[:-1], 1 / (daily_returns + 1) - 1, 0)

        total_buy_capture = np.zeros_like(close_prices)
        total_short_capture = np.zeros_like(close_prices)

        total_buy_capture[1:] = np.cumsum(buy_returns) * 100
        total_short_capture[1:] = np.cumsum(short_returns) * 100

        # Add SMA traces
        fig.add_trace(go.Scatter(x=df.index, y=sma1, mode='lines', name=f'SMA {sma_day_1}'))
        fig.add_trace(go.Scatter(x=df.index, y=sma2, mode='lines', name=f'SMA {sma_day_2}'))

        # Add Total Buy Capture and Total Short Capture traces
        fig.add_trace(go.Scatter(
            x=df.index[max(sma_day_1, sma_day_2):],
            y=total_buy_capture[max(sma_day_1, sma_day_2):],
            mode='lines',
            name='Total Buy Capture %'
        ))
        fig.add_trace(go.Scatter(
            x=df.index[max(sma_day_1, sma_day_2):],
            y=total_short_capture[max(sma_day_1, sma_day_2):],
            mode='lines',
            name='Total Short Capture %'
        ))

        # Prepare the text for the total capture stats
        total_capture_stats = [
            f"Total Buy Capture %: {total_buy_capture[-1]:.2f}%",
            html.Br(),
            f"Total Short Capture %: {total_short_capture[-1]:.2f}%"
        ]

    # Customize layout
    fig.update_layout(
        title='S&P 500 Closing Prices, SMAs, and Total Capture Over Time',
        xaxis_title='Trading Day',
        yaxis_title='S&P 500 Closing Price',
        hovermode=None,  # Disable mouse-over effects
        uirevision='static'
    )

    return fig, total_capture_stats

# Run the app
if __name__ == '__main__':
    app.run_server(debug=True)