import pytest
import pandas as pd
from app.engines.market_data import (
    MarketDataEngine,
    closed_candle_frame,
    normalize_yfinance_symbol,
    _prune_caches,
    _OHLCV_CACHE,
    _TICKER_CACHE,
)


def test_symbol_normalization():
    assert normalize_yfinance_symbol("BTC/USDT", "crypto") == "BTC-USD"
    assert normalize_yfinance_symbol("ETH/USDT", "crypto") == "ETH-USD"
    assert normalize_yfinance_symbol("XAUUSD", "forex") == "GC=F"
    assert normalize_yfinance_symbol("EURUSD", "forex") == "EURUSD=X"
    assert normalize_yfinance_symbol("AAPL", "stock") == "AAPL"


@pytest.mark.anyio
async def test_ohlcv_caching():
    mde = MarketDataEngine()
    df_fake = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [105.0, 106.0, 107.0, 108.0, 109.0],
            "low": [95.0, 96.0, 97.0, 98.0, 99.0],
            "close": [103.0, 104.0, 105.0, 106.0, 107.0],
            "volume": [1000, 1100, 1200, 1300, 1400],
        },
        index=pd.date_range("2026-01-01", periods=5, freq="h"),
    )

    import time
    now = time.time()
    _OHLCV_CACHE["TEST/USDT:1h:crypto:binance:300"] = (now, df_fake)

    cached_df = await mde.get_ohlcv("TEST/USDT", "1h", "crypto", limit=300)
    assert not cached_df.empty
    assert len(cached_df) == 5
    assert float(cached_df["close"].iloc[-1]) == 107.0


def test_cache_pruning():
    import time
    now = time.time()
    _TICKER_CACHE["OLD_SYM:crypto"] = (now - 100.0, {"price": 10.0})
    for i in range(350):
        _TICKER_CACHE[f"SYM_{i}:crypto"] = (now - 100.0, {"price": float(i)})

    _prune_caches(now)
    assert len(_TICKER_CACHE) < 350


def test_closed_candle_frame_removes_forming_row_and_keeps_requested_lookback():
    now = pd.Timestamp("2026-08-27T07:30:00Z")
    index = pd.date_range("2026-08-27T04:00:00Z", periods=4, freq="h")
    frame = pd.DataFrame(
        {
            "open": [1, 2, 3, 4],
            "high": [2, 3, 4, 5],
            "low": [0, 1, 2, 3],
            "close": [1.5, 2.5, 3.5, 4.5],
            "volume": [10, 10, 10, 10],
        },
        index=index,
    )

    closed = closed_candle_frame(frame, "1h", limit=3, now=now)

    assert list(closed.index) == list(index[:3])
    assert closed.index[-1] == pd.Timestamp("2026-08-27T06:00:00Z")
