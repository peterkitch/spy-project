import yfinance as yf
import plotly.graph_objects as go
from dash import Dash, dcc, html
from dash.dependencies import Input, Output, State
import dash_bootstrap_components as dbc
import pandas as pd
import numpy as np

# Fetch data
df = yf.download('^GSPC', period='max', interval='1d')

# Set the maximum window size for SMAs
max_window_size = 20

# Calculate SMAs for window sizes 1 to the maximum window size
sma_columns = {
    f'SMA_{i+1}': df['Close'].rolling(window=i+1).mean()
    for i in range(max_window_size)
}

# Concatenate the SMA columns with the original DataFrame
df = pd.concat([df, pd.DataFrame(sma_columns)], axis=1)

# Initialize the Dash app with a dark theme and custom styles
app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY])
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
                dcc.Graph(id='chart')
            ], width=12)
        ]),
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader('Buy Pair'),
                    dbc.CardBody([
                        html.Div([
                            html.Label(f'Enter 1st SMA Day (1-{max_window_size}) for Buy Pair:', className='mb-1'),
                            dcc.Input(id='sma-input-1', type='number', value=1, min=1, max=max_window_size, step=1, className='form-control'),
                            html.Div(id='sma-input-1-error', className='text-danger')
                        ], className='mb-3'),
                        html.Div([
                            html.Label(f'Enter 2nd SMA Day (1-{max_window_size}) for Buy Pair:', className='mb-1'),
                            dcc.Input(id='sma-input-2', type='number', value=3, min=1, max=max_window_size, step=1, className='form-control'),
                            html.Div(id='sma-input-2-error', className='text-danger')
                        ], className='mb-3')
                    ])
                ], className='mb-3'),
                dbc.Card([
                    dbc.CardHeader('Short Pair'),
                    dbc.CardBody([
                        html.Div([
                            html.Label(f'Enter 3rd SMA Day (1-{max_window_size}) for Short Pair:', className='mb-1'),
                            dcc.Input(id='sma-input-3', type='number', value=13, min=1, max=max_window_size, step=1, className='form-control'),
                            html.Div(id='sma-input-3-error', className='text-danger')
                        ], className='mb-3'),
                        html.Div([
                            html.Label(f'Enter 4th SMA Day (1-{max_window_size}) for Short Pair:', className='mb-1'),
                            dcc.Input(id='sma-input-4', type='number', value=5, min=1, max=max_window_size, step=1, className='form-control'),
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
                            html.Div(id='trade-signal')
                        ]),
                        id='strategy-collapse',
                        is_open=True
                    )
                ])
            ], width=12)
        ]),
        dcc.Loading(
            id="loading-1",
            type="default",
            children=html.Div(id="loading-output")
        )
    ]
)
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
        if sma_input is None or sma_input < 1 or sma_input > max_window_size:
            input_classes.append('form-control is-invalid')
            error_messages.append('Please enter a valid SMA day.')
        else:
            input_classes.append('form-control')
            error_messages.append('')

    return input_classes + error_messages

# Callback to update the dynamic trading strategy
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
     Output('trade-signal', 'children')],
    [Input('sma-input-1', 'value'),
     Input('sma-input-2', 'value'),
     Input('sma-input-3', 'value'),
     Input('sma-input-4', 'value')]
)
def update_dynamic_strategy(sma_day_1, sma_day_2, sma_day_3, sma_day_4):
    # Check if all SMA input values are valid
    if any(sma_day is None for sma_day in [sma_day_1, sma_day_2, sma_day_3, sma_day_4]):
        return [''] * 10

    # Calculate the most productive buy and short pairs
    buy_pairs = [(i, j) for i in range(1, max_window_size + 1) for j in range(1, max_window_size + 1) if i > j]
    short_pairs = buy_pairs

    buy_results = []
    short_results = []

    for pair in buy_pairs:
        sma1 = df.loc[:, f'SMA_{pair[0]}']
        sma2 = df.loc[:, f'SMA_{pair[1]}']
        
        buy_signals = (sma1 > sma2).astype(int)
        entry_signals = (buy_signals - buy_signals.shift(1)).astype(bool)
        buy_returns = df['Close'].pct_change()[entry_signals].dropna()
        buy_results.append((pair, buy_returns.sum()))
        
        short_signals = (sma1 < sma2).astype(int)
        entry_signals = (short_signals - short_signals.shift(1)).astype(bool)
        short_returns = -df['Close'].pct_change()[entry_signals].dropna()
        short_results.append((pair, short_returns.sum()))

    most_productive_buy_pair = max(buy_results, key=lambda x: x[1])[0]
    most_productive_short_pair = max(short_results, key=lambda x: x[1])[0]

    # Calculate buy and short leader signals
    sma1_buy_leader = df.loc[:, f'SMA_{most_productive_buy_pair[0]}']
    sma2_buy_leader = df.loc[:, f'SMA_{most_productive_buy_pair[1]}']
    buy_signals_leader = (sma1_buy_leader > sma2_buy_leader).astype(int)
    entry_signals_buy_leader = (buy_signals_leader - buy_signals_leader.shift(1)).astype(bool)
    buy_returns_leader = df['Close'].pct_change()[entry_signals_buy_leader].dropna()

    sma1_short_leader = df.loc[:, f'SMA_{most_productive_short_pair[0]}']
    sma2_short_leader = df.loc[:, f'SMA_{most_productive_short_pair[1]}']
    short_signals_leader = (sma1_short_leader < sma2_short_leader).astype(int)
    entry_signals_short_leader = (short_signals_leader - short_signals_leader.shift(1)).astype(bool)
    short_returns_leader = -df['Close'].pct_change()[entry_signals_short_leader].dropna()

    # Determine trading direction and active leader returns
    if buy_returns_leader.size > 0 and short_returns_leader.size > 0:
        if buy_returns_leader.sum() > short_returns_leader.sum():
            trading_direction = "Trading Direction: Buy (Both triggers active, Buy leading)"
            active_leader_returns = buy_returns_leader
        else:
            trading_direction = "Trading Direction: Short (Both triggers active, Short leading)"
            active_leader_returns = short_returns_leader
    elif buy_returns_leader.size > 0:
        trading_direction = "Trading Direction: Buy"
        active_leader_returns = buy_returns_leader
    elif short_returns_leader.size > 0:
        trading_direction = "Trading Direction: Short"
        active_leader_returns = short_returns_leader
    else:
        trading_direction = "Trading Direction: Cash (No active triggers)"
        active_leader_returns = pd.Series([0])

    # Calculate performance metrics
    most_productive_buy_pair_text = f"Most Productive Buy Pair: SMA {most_productive_buy_pair[0]} / SMA {most_productive_buy_pair[1]}"
    most_productive_short_pair_text = f"Most Productive Short Pair: SMA {most_productive_short_pair[0]} / SMA {most_productive_short_pair[1]}"
    avg_capture_buy_leader = f"Avg. Capture % for Buy Leader: {buy_returns_leader.mean() * 100:.2f}%" if buy_returns_leader.size > 0 else "Avg. Capture % for Buy Leader: N/A"
    total_capture_buy_leader = f"Total Capture for Buy Leader: {buy_returns_leader.sum() * 100:.2f}%" if buy_returns_leader.size > 0 else "Total Capture for Buy Leader: N/A"
    avg_capture_short_leader = f"Avg. Capture % for Short Leader: {short_returns_leader.mean() * 100:.2f}%" if short_returns_leader.size > 0 else "Avg. Capture % for Short Leader: N/A"
    total_capture_short_leader = f"Total Capture for Short Leader: {short_returns_leader.sum() * 100:.2f}%" if short_returns_leader.size > 0 else "Total Capture for Short Leader: N/A"
    performance_expectation = f"Performance Expectation: {active_leader_returns.mean() * 100:.2f}%" if active_leader_returns.size > 0 else "Performance Expectation: N/A"
    confidence_percentage = f"Confidence Percentage: {(active_leader_returns > 0).mean() * 100:.2f}%" if active_leader_returns.size > 0 else "Confidence Percentage: N/A"

    # Generate trade signal
    if trading_direction == "Trading Direction: Buy":
        trade_signal = f"Trade Signal for Next Day: Buy if S&P 500 closes above {df['Close'][-1]:.2f}"
    elif trading_direction == "Trading Direction: Short":
        trade_signal = f"Trade Signal for Next Day: Short if S&P 500 closes below {df['Close'][-1]:.2f}"
    else:
        trade_signal = "Trade Signal for Next Day: No trade"

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
        trade_signal
    )

# Callback function to update the chart and calculation components based on user input
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
    [Input('sma-input-1', 'value'),
     Input('sma-input-2', 'value'),
     Input('sma-input-3', 'value'),
     Input('sma-input-4', 'value')]
)
def update_chart(sma_day_1, sma_day_2, sma_day_3, sma_day_4):
    # Check if all SMA input values are valid
    if any(sma_day is None for sma_day in [sma_day_1, sma_day_2, sma_day_3, sma_day_4]):
        # Return an empty figure and default values for other outputs
        fig = go.Figure()
        trigger_days_buy = "Buy Trigger Days: N/A"
        win_ratio_buy = "Buy Win Ratio: N/A"
        avg_daily_capture_buy = "Buy Avg. Daily Capture: N/A"
        total_capture_buy = "Buy Total Capture: N/A"
        trigger_days_short = "Short Trigger Days: N/A"
        win_ratio_short = "Short Win Ratio: N/A"
        avg_daily_capture_short = "Short Avg. Daily Capture: N/A"
        total_capture_short = "Short Total Capture: N/A"
        return fig, trigger_days_buy, win_ratio_buy, avg_daily_capture_buy, total_capture_buy, trigger_days_short, win_ratio_short, avg_daily_capture_short, total_capture_short

    # Create the chart figure
    fig = go.Figure()

    # Add S&P 500 closing prices trace
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name='S&P 500 Close'))

    # Calculate and Add Buy and Short signals
    sma1_buy = df.loc[:, f'SMA_{sma_day_1}']
    sma2_buy = df.loc[:, f'SMA_{sma_day_2}']
    buy_signals = (sma1_buy > sma2_buy).astype(int)
    buy_signals_shifted = buy_signals.shift(1, fill_value=0)
    entry_signals_buy = (buy_signals - buy_signals_shifted).astype(bool)

    sma1_short = df.loc[:, f'SMA_{sma_day_3}']
    sma2_short = df.loc[:, f'SMA_{sma_day_4}']
    short_signals = (sma1_short < sma2_short).astype(int)
    short_signals_shifted = short_signals.shift(1, fill_value=0)
    entry_signals_short = (short_signals - short_signals_shifted).astype(bool)

    daily_returns = df['Close'].pct_change()
    buy_returns = daily_returns.copy()
    buy_returns[~entry_signals_buy] = 0
    short_returns = -daily_returns.copy()
    short_returns[~entry_signals_short] = 0

    total_buy_capture = buy_returns.cumsum()
    total_short_capture = short_returns.cumsum()

    # Add SMA traces
    fig.add_trace(go.Scatter(x=df.index, y=sma1_buy, mode='lines', name=f'SMA {sma_day_1} (Buy)'))
    fig.add_trace(go.Scatter(x=df.index, y=sma2_buy, mode='lines', name=f'SMA {sma_day_2} (Buy)'))
    fig.add_trace(go.Scatter(x=df.index, y=sma1_short, mode='lines', name=f'SMA {sma_day_3} (Short)'))
    fig.add_trace(go.Scatter(x=df.index, y=sma2_short, mode='lines', name=f'SMA {sma_day_4} (Short)'))

    # Add Total Buy Capture and Total Short Capture traces
    fig.add_trace(go.Scatter(x=df.index, y=total_buy_capture, mode='lines', name='Total Buy Capture'))
    fig.add_trace(go.Scatter(x=df.index, y=total_short_capture, mode='lines', name='Total Short Capture'))

    # Customize layout
    fig.update_layout(
        title='S&P 500 Closing Prices, SMAs, and Total Capture Over Time',
        xaxis_title='Trading Day',
        yaxis_title='S&P 500 Closing Price',
        hovermode='x',
        uirevision='static',
        template='plotly_dark'
    )

    # Calculate trigger days for Buy and Short
    trigger_days_buy = f"Buy Trigger Days: {entry_signals_buy.sum()}"
    trigger_days_short = f"Short Trigger Days: {entry_signals_short.sum()}"

    # Calculate win ratios for Buy and Short
    win_ratio_buy = f"Buy Win Ratio: {(buy_returns > 0).mean() * 100:.2f}%" if entry_signals_buy.any() else "Buy Win Ratio: N/A"
    win_ratio_short = f"Short Win Ratio: {(short_returns > 0).mean() * 100:.2f}%" if entry_signals_short.any() else "Short Win Ratio: N/A"

    # Calculate average daily capture and total capture for Buy and Short
    avg_daily_capture_buy = f"Buy Avg. Daily Capture: {buy_returns[buy_returns != 0].mean() * 100:.2f}%" if (buy_returns != 0).any() else "Buy Avg. Daily Capture: N/A"
    total_capture_buy = f"Buy Total Capture: {total_buy_capture[-1] * 100:.2f}%"
    avg_daily_capture_short = f"Short Avg. Daily Capture: {short_returns[short_returns != 0].mean() * 100:.2f}%" if (short_returns != 0).any() else "Short Avg. Daily Capture: N/A"
    total_capture_short = f"Short Total Capture: {total_short_capture[-1] * 100:.2f}%"

    return fig, trigger_days_buy, win_ratio_buy, avg_daily_capture_buy, total_capture_buy, trigger_days_short, win_ratio_short, avg_daily_capture_short, total_capture_short

# Run the app
if __name__ == '__main__':
    app.run_server(debug=True)