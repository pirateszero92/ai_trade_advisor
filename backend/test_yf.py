import yfinance as yf
t = yf.Ticker("BTC-USD")
df = t.history(period="7d", interval="1h")
print("yfinance BTC-USD shape:", df.shape)
if not df.empty:
    print(df[["Open", "High", "Low", "Close", "Volume"]].tail(3))
