import yfinance as yf
import plotly.graph_objects as go
import dash
from dash import dcc
from dash import html
from dash.dependencies import Input, Output
import pandas as pd
import webbrowser

# Fetch data
df = yf.download('^GSPC', start='1927-12-30')

# Calculate SMAs for window sizes 1 to the total number of trading days
total_trading_days = len(df)
sma_columns = {}
for window in range(1, total_trading_days + 1):
    column_name = f'SMA_{window}'
    sma_columns[column_name] = df['Close'].rolling(window=window).mean()

# Concatenate the SMA columns with the original DataFrame
df = pd.concat([df, pd.DataFrame(sma_columns)], axis=1)

# Initialize the Dash app
app = dash.Dash(__name__)

# Define the app layout
app.layout = html.Div([
    dcc.Graph(id='chart'),
    html.Div([
        html.Label(f'Enter SMA Day from 1 - {total_trading_days}:'),
        dcc.Input(id='sma-input', type='number', value=50, min=1, max=total_trading_days, step=1)
    ])
])

# Callback function to update the chart based on user input
@app.callback(Output('chart', 'figure'),
              [Input('sma-input', 'value')])
def update_chart(sma_day):
    # Create the chart figure
    fig = go.Figure()

    # Add S&P 500 closing prices trace
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name='S&P 500 Close'))

    # Add the selected SMA trace
    column_name = f'SMA_{sma_day}'
    trace_name = f'{sma_day}-day SMA'
    fig.add_trace(go.Scatter(x=df.index, y=df[column_name], mode='lines', name=trace_name))

    # Customize layout
    fig.update_layout(
        title='S&P 500 Closing Prices and Selected SMA Over Time',
        xaxis_title='Date',
        yaxis_title='Price',
        xaxis=dict(
            tickformat='%m/%d/%Y',  # Set the date format for x-axis tick labels
        ),
        hovermode='x unified'  # Unified hover
    )

    # Customize hover template for better readability
    fig.update_traces(
        hovertemplate='Date: %{x|%m/%d/%Y}<br>Price: %{y:.2f}'
    )

    return fig

# Run the app
if __name__ == '__main__':
    webbrowser.open_new('http://127.0.0.1:8050/')
    app.run_server(debug=True)