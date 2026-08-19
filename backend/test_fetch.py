import asyncio
from app.engines.market_data import MarketDataEngine

async def main():
    m = MarketDataEngine()
    df = await m.get_ohlcv("BTC/USDT", "1h", "crypto", "binance", limit=10)
    print("Crypto DataFrame shape:", df.shape)
    if not df.empty:
        print(df.tail(2))
    
    df_stock = await m.get_ohlcv("AAPL", "1d", "stock", limit=5)
    print("Stock AAPL shape:", df_stock.shape)

if __name__ == "__main__":
    asyncio.run(main())
