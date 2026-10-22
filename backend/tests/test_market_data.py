import pytest
import pandas as pd
from unittest.mock import AsyncMock
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


@pytest.mark.anyio
async def test_ohlcv_keeps_forming_candle_only_when_explicitly_requested():
    mde = MarketDataEngine()
    current_bucket = pd.Timestamp.now(tz="UTC").floor("15min")
    index = pd.date_range(end=current_bucket, periods=5, freq="15min")
    frame = pd.DataFrame(
        {
            "open": [1, 2, 3, 4, 5],
            "high": [2, 3, 4, 5, 6],
            "low": [0, 1, 2, 3, 4],
            "close": [1.5, 2.5, 3.5, 4.5, 5.5],
            "volume": [10, 10, 10, 10, 10],
        },
        index=index,
    )
    cache_key = "FORMING/USDT:15m:crypto:binance:5"
    import time

    _OHLCV_CACHE[cache_key] = (time.time(), frame)
    try:
        closed = await mde.get_ohlcv(
            "FORMING/USDT", "15m", "crypto", limit=5
        )
        chart = await mde.get_ohlcv(
            "FORMING/USDT", "15m", "crypto", limit=5, include_forming=True
        )
    finally:
        _OHLCV_CACHE.pop(cache_key, None)

    assert list(closed.index) == list(index[:-1])
    assert list(chart.index) == list(index)
    assert float(chart.iloc[-1]["close"]) == 5.5


@pytest.mark.anyio
async def test_ohlcv_rejects_conflicting_candle_policies():
    with pytest.raises(ValueError, match="mutually exclusive"):
        await MarketDataEngine().get_ohlcv(
            "BTC/USDT",
            "15m",
            "crypto",
            limit=5,
            closed_only=True,
            include_forming=True,
        )


@pytest.mark.anyio
async def test_stock_without_alpaca_credentials_is_explicitly_watch_only(monkeypatch):
    engine = MarketDataEngine()
    monkeypatch.setattr(engine.cfg, "alpaca_api_key", "")
    monkeypatch.setattr(engine.cfg, "alpaca_api_secret", "")
    current_bucket = pd.Timestamp.now(tz="UTC").floor("15min")
    frame = pd.DataFrame(
        {
            "open": [100.0] * 8,
            "high": [101.0] * 8,
            "low": [99.0] * 8,
            "close": [100.5] * 8,
            "volume": [1000.0] * 8,
        },
        index=pd.date_range(end=current_bucket, periods=8, freq="15min"),
    )
    monkeypatch.setattr(engine, "_get_yfinance", AsyncMock(return_value=frame))
    _OHLCV_CACHE.clear()

    result = await engine.get_ohlcv(
        "AAPL", "15m", "stock", exchange="alpaca", limit=6,
        include_forming=True,
    )

    assert result.attrs["market_data_status"]["market"] == "US_EQUITY"
    assert result.attrs["market_data_status"]["data_provider"] == "yfinance_watch_only"
    assert result.attrs["market_data_status"]["execution_mode"] == "shadow_manual"


@pytest.mark.anyio
async def test_bybit_fallback_tagged_accurately(monkeypatch):
    engine = MarketDataEngine()

    class MockResp:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json = json_data

        def json(self):
            return self._json

    class MockClient:
        async def get(self, url, *args, **kwargs):
            if "bybit.com" in str(url):
                base_ms = 1700000000000
                klines = []
                for i in range(6):
                    t = base_ms + i * 15 * 60 * 1000
                    klines.append([str(t), "100.0", "101.0", "99.0", "100.5", "500.0", "50250.0"])
                return MockResp(200, {"result": {"list": klines}})
            # Binance hosts fail
            return MockResp(500, {})

    monkeypatch.setattr("app.engines.market_data.get_shared_http_client", lambda: MockClient())
    _OHLCV_CACHE.clear()

    df = await engine._get_crypto("BTC/USDT", "15m", "binance", 10)
    assert not df.empty
    assert df["flow_source"].iloc[-1] == "bybit_rest_ohlcv"


