import yfinance as yf
import plotly.graph_objects as go
from dash import Dash, dcc, html
from dash.dependencies import Input, Output
import pandas as pd

# Fetch data
df = yf.download('^GSPC', start='1927-12-30')

# Set the maximum window size for SMAs to the total number of trading days
max_window_size = len(df)

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
        dcc.Input(id='sma-input-1', type='number', value=50, min=1, max=max_window_size, step=1),
    ]),
    html.Div([  # New input field for the second SMA
        html.Label(f'Enter 2nd SMA Day from 1 - {max_window_size}:'),
        dcc.Input(id='sma-input-2', type='number', value=200, min=1, max=max_window_size, step=1),
    ]),
    html.Div(id='error-message', style={'color': 'red'}),
    html.Div(id='account-value-text')  # Div to display the account value
])

# Callback function to update the chart based on user input
@app.callback(
    [Output('chart', 'figure'),
     Output('error-message', 'children'),
     Output('account-value-text', 'children')],  # Outputs include account value text
    [Input('sma-input-1', 'value'),
     Input('sma-input-2', 'value'),
     Input('initial-investment', 'value')]  # Inputs include initial investment
)
def update_chart(sma_day_1, sma_day_2, initial_investment):
    error_message = ''

    # Create the chart figure
    fig = go.Figure()

    # Add S&P 500 closing prices trace
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name='S&P 500 Close'))

    # Calculate and Add Account Balance as a trace, if SMAs are valid
    if sma_day_1 is not None and sma_day_2 is not None and 1 <= sma_day_1 <= max_window_size and 1 <= sma_day_2 <= max_window_size:
        account_balance = [initial_investment]
        for i in range(1, len(df)):
            if pd.isna(df[f'SMA_{sma_day_1}'].iloc[i]) or pd.isna(df[f'SMA_{sma_day_2}'].iloc[i]):
                account_balance.append(account_balance[-1])
            elif df[f'SMA_{sma_day_1}'].iloc[i-1] > df[f'SMA_{sma_day_2}'].iloc[i-1]:
                # Calculate gain/loss percentage and update account balance
                gain_loss = df['Close'].iloc[i] / df['Close'].iloc[i-1]
                account_balance.append(account_balance[-1] * gain_loss)
            else:
                # No change in account balance
                account_balance.append(account_balance[-1])

        fig.add_trace(go.Scatter(x=df.index, y=account_balance, mode='lines', name='Account Balance'))
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
    account_value_text = f"Account value as of close on {df.index[-1].strftime('%Y-%m-%d')}: {account_balance[-1]:.2f}" if 'account_balance' in locals() else "Invalid SMA Days"

    return fig, error_message, account_value_text

# Run the app
if __name__ == '__main__':
    app.run_server(debug=True)
