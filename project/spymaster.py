import yfinance as yf
import plotly.graph_objects as go
from dash import Dash, dcc, html
from dash.dependencies import Input, Output
import pandas as pd
import numpy as np
from itertools import combinations
from concurrent.futures import ThreadPoolExecutor, as_completed

# Fetch data
df = yf.download('^GSPC', start='1927-12-30')

# Set the maximum window size for SMAs
max_window_size = 20

# Calculate SMAs for window sizes 1 to the maximum window size
sma_columns = {}
for window in range(1, max_window_size + 1):
    column_name = f'SMA_{window}'
    sma_columns[column_name] = df['Close'].rolling(window=window).mean()

# Concatenate the SMA columns with the original DataFrame
df = pd.concat([df, pd.DataFrame(sma_columns)], axis=1)

# Initialize the Dash app
app = Dash(__name__)

# Define the app layout
app.layout = html.Div([
    html.Div([  # Initial Investment Input
        html.Label('Enter Initial Investment:'),
        dcc.Input(id='initial-investment', type='number', value=10000, step=100),
    ]),
    dcc.Graph(id='chart'),
    html.Div([
        html.Label(f'Enter 1st SMA Day from 1 - {max_window_size}:'),
        dcc.Input(id='sma-input-1', type='number', value=10, min=1, max=max_window_size, step=1),
    ]),
    html.Div([  # New input field for the second SMA
        html.Label(f'Enter 2nd SMA Day from 1 - {max_window_size}:'),
        dcc.Input(id='sma-input-2', type='number', value=20, min=1, max=max_window_size, step=1),
    ]),
    html.Div(id='error-message', style={'color': 'red'}),
    html.Div(id='account-value-text'),  # Div to display the account value
    html.Div(id='optimal-sma-text')  # Div to display the optimal SMA combination
])

# Callback function to update the chart based on user input
@app.callback(
    [Output('chart', 'figure'),
     Output('error-message', 'children'),
    [Input('sma-input-1', 'value'),
     Input('sma-input-2', 'value'),
)
def update_chart(sma_day_1, sma_day_2, initial_investment):
    error_message = ''

    # Create the chart figure
    fig = go.Figure()

    # Add S&P 500 closing prices trace
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name='S&P 500 Close'))

    # Calculate and Add Buy Account Balance as a trace, if SMAs are valid
    if sma_day_1 is not None and sma_day_2 is not None and 1 <= sma_day_1 <= max_window_size and 1 <= sma_day_2 <= max_window_size:
        buy_account_balance = [initial_investment]
        short_account_balance = [initial_investment]
        for i in range(1, len(df)):
            if pd.isna(df[f'SMA_{sma_day_1}'].iloc[i]) or pd.isna(df[f'SMA_{sma_day_2}'].iloc[i]):
                buy_account_balance.append(buy_account_balance[-1])
                short_account_balance.append(short_account_balance[-1])
            elif df[f'SMA_{sma_day_1}'].iloc[i-1] > df[f'SMA_{sma_day_2}'].iloc[i-1]:
                gain_loss_buy = df['Close'].iloc[i] / df['Close'].iloc[i-1]
                gain_loss_short = df['Close'].iloc[i] / df['Close'].iloc[i-1]
                buy_account_balance.append(buy_account_balance[-1] * gain_loss_buy)
                short_account_balance.append(short_account_balance[-1] / gain_loss_short)
            else:
                buy_account_balance.append(buy_account_balance[-1])
                short_account_balance.append(short_account_balance[-1])

        fig.add_trace(go.Scatter(x=df.index, y=buy_account_balance, mode='lines', name='Buy Account Balance'))
        fig.add_trace(go.Scatter(x=df.index, y=short_account_balance, mode='lines', name='Short Account Balance'))
    else:
        error_message = f'Please enter valid SMA days between 1 and {max_window_size}.'

    # Customize layout
    fig.update_layout(
        title='S&P 500 Closing Prices, SMAs, and Account Balance Over Time',
        xaxis_title='Date',
        yaxis_title='Value',
        xaxis=dict(
            tickformat='%m/%d/%Y',
        ),
        hovermode='x unified',
        uirevision='static'
    )

    # Customize hover template for better readability
    fig.update_traces(
        hovertemplate='Date: %{x|%m/%d/%Y}<br>Value: %{y:.2f}'
    )

    # Prepare the text for the most recent account value
    account_value_text = [
        f"Buy Account Value as of close on {df.index[-1].strftime('%Y-%m-%d')}: {buy_account_balance[-1]:.2f}",
        html.Br(),
        f"Short Account Value as of close on {df.index[-1].strftime('%Y-%m-%d')}: {short_account_balance[-1]:.2f}"
    ] if 'buy_account_balance' in locals() and 'short_account_balance' in locals() else "Invalid SMA Days"

    return fig, error_message, account_value_text

# Callback function to update the optimal SMA combination text
@app.callback(Output('optimal-sma-text', 'children'),
              [Input('initial-investment', 'value')])
def update_optimal_sma_text(initial_investment):
    optimal_buy_sma_combination, max_buy_account_balance, optimal_short_sma_combination, max_short_account_balance = find_optimal_sma_combination(initial_investment)
    return [
        f"Optimal Buy SMA Combination: {optimal_buy_sma_combination}, Maximum Buy Account Balance: {max_buy_account_balance:.2f}",
        html.Br(),
        f"Optimal Short SMA Combination: {optimal_short_sma_combination}, Maximum Short Account Balance: {max_short_account_balance:.2f}"
    ]

def calculate_account_balance(sma1, sma2, initial_investment, short=False):
    close_prices = df['Close'].values
    sma1_values = df[f'SMA_{sma1}'].values
    sma2_values = df[f'SMA_{sma2}'].values

    account_balance = np.zeros_like(close_prices)
    account_balance[0] = initial_investment

    for i in range(1, len(close_prices)):
        if np.isnan(sma1_values[i]) or np.isnan(sma2_values[i]):
            account_balance[i] = account_balance[i-1]
        elif sma1_values[i-1] > sma2_values[i-1]:
            if short:
                gain_loss = close_prices[i] / close_prices[i-1]
                account_balance[i] = account_balance[i-1] / gain_loss
            else:
                gain_loss = close_prices[i] / close_prices[i-1]
                account_balance[i] = account_balance[i-1] * gain_loss
        else:
            account_balance[i] = account_balance[i-1]

    return account_balance[-1]

def find_optimal_sma_combination(initial_investment):
    max_buy_account_balance = 0
    optimal_buy_sma_combination = None
    max_short_account_balance = 0
    optimal_short_sma_combination = None
    total_combinations = sum(1 for _ in combinations(range(1, max_window_size + 1), 2))
    completed_combinations = 0

    with ThreadPoolExecutor() as executor:
        futures = []
        for sma1, sma2 in combinations(range(1, max_window_size + 1), 2):
            buy_future = executor.submit(calculate_account_balance, sma1, sma2, initial_investment, short=False)
            short_future = executor.submit(calculate_account_balance, sma1, sma2, initial_investment, short=True)
            futures.append((buy_future, short_future, sma1, sma2))

        for buy_future, short_future, sma1, sma2 in futures:
            buy_account_balance = buy_future.result()
            short_account_balance = short_future.result()
            completed_combinations += 1
            progress = completed_combinations / total_combinations * 100

            if buy_account_balance > max_buy_account_balance:
                max_buy_account_balance = buy_account_balance
                optimal_buy_sma_combination = (sma1, sma2)

            if short_account_balance > max_short_account_balance:
                max_short_account_balance = short_account_balance
                optimal_short_sma_combination = (sma1, sma2)

            print(f"\rProgress: {progress:.2f}%", end="", flush=True)

    print("\nSearch completed.")
    print(f"Optimal Buy SMA Combination: {optimal_buy_sma_combination}")
    print(f"Maximum Buy Account Balance: {max_buy_account_balance:.2f}")
    print(f"Optimal Short SMA Combination: {optimal_short_sma_combination}")
    print(f"Maximum Short Account Balance: {max_short_account_balance:.2f}")

    return optimal_buy_sma_combination, max_buy_account_balance, optimal_short_sma_combination, max_short_account_balance

# Run the app
if __name__ == '__main__':
    app.run_server(debug=True)