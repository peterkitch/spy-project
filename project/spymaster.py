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
    dcc.Graph(id='chart'),
    html.Div([
        html.Label(f'Enter SMA Day from 1 - {max_window_size}:'),
        dcc.Input(id='sma-input', type='number', value=50, min=1, max=max_window_size, step=1),
        html.Div(id='error-message', style={'color': 'red'})
    ])
])

# Callback function to update the chart based on user input
@app.callback(
    [Output('chart', 'figure'),
     Output('error-message', 'children')],
    [Input('sma-input', 'value')]
)
def update_chart(sma_day):
    error_message = ''

    # Create the chart figure
    fig = go.Figure()

    # Add S&P 500 closing prices trace
    fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name='S&P 500 Close'))

    # Add the selected SMA trace if a valid SMA day is provided
    if sma_day is not None and 1 <= sma_day <= max_window_size:
        column_name = f'SMA_{sma_day}'
        trace_name = f'{sma_day}-day SMA'
        fig.add_trace(go.Scatter(x=df.index, y=df[column_name], mode='lines', name=trace_name))
    elif sma_day is not None:
        error_message = f'Please enter a valid SMA day between 1 and {max_window_size}.'

    # Customize layout
    fig.update_layout(
        title='S&P 500 Closing Prices and Selected SMA Over Time',
        xaxis_title='Date',
        yaxis_title='Price',
        xaxis=dict(
            tickformat='%m/%d/%Y',  # Set the date format for x-axis tick labels
        ),
        hovermode='x unified',  # Unified hover
        uirevision='static'  # Maintain zoom and scale on update
    )

    # Customize hover template for better readability
    fig.update_traces(
        hovertemplate='Date: %{x|%m/%d/%Y}<br>Price: %{y:.2f}'
    )

    return fig, error_message

# Run the app
if __name__ == '__main__':
    app.run_server(debug=True)