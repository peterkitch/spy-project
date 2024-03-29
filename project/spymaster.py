import yfinance as yf
import plotly.graph_objects as go

# Fetch data
df = yf.download('^GSPC', start='1927-12-30')

# Calculate SMAs
df['SMA_50'] = df['Close'].rolling(window=50).mean()
df['SMA_100'] = df['Close'].rolling(window=100).mean()
df['SMA_200'] = df['Close'].rolling(window=200).mean()

# Add S&P 500 closing prices trace
fig = go.Figure()
fig.add_trace(go.Scatter(x=df.index, y=df['Close'], mode='lines', name='S&P 500 Close'))

# Add SMA traces
fig.add_trace(go.Scatter(x=df.index, y=df['SMA_50'], mode='lines', name='50-day SMA'))
fig.add_trace(go.Scatter(x=df.index, y=df['SMA_100'], mode='lines', name='100-day SMA'))
fig.add_trace(go.Scatter(x=df.index, y=df['SMA_200'], mode='lines', name='200-day SMA'))

# Customize layout
fig.update_layout(
    title='S&P 500 Closing Prices and SMAs Over Time',
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

# Show figure
fig.show()

# Save to HTML (Optional)
fig.write_html('sp500_closing_prices_with_smas.html')
