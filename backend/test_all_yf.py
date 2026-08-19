import yfinance as yf

for sym in ["BTC-USD", "ETH-USD", "GC=F", "EURUSD=X", "AAPL"]:
    df = yf.Ticker(sym).history(period="5d", interval="1h")
    print(f"{sym}: {df.shape} candles, Last Close: {df['Close'].iloc[-1]:.2f}")
