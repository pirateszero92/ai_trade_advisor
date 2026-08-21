"""Market Data Engine with multi-source fallback (CCXT, MT5, yfinance)."""

from __future__ import annotations

import asyncio
from typing import Literal, Optional
import numpy as np
import pandas as pd
import yfinance as yf
from loguru import logger

from app.core.config import get_settings


CCXT_TF_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "1H": "1h", "2h": "2h", "4h": "4h", "4H": "4h",
    "1d": "1d", "1D": "1d", "1w": "1w", "1W": "1w", "1M": "1M",
}

YF_TF_MAP = {
    "1m": "1m", "2m": "2m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "1H": "1h", "4h": "1h", "4H": "1h",
    "1d": "1d", "1D": "1d", "1w": "1wk", "1W": "1wk", "1M": "1mo",
}


def normalize_yfinance_symbol(symbol: str, market_type: str) -> str:
    """Normalize any symbol format to Yahoo Finance ticker."""
    s = symbol.upper().replace("/", "").replace("-", "")

    if market_type == "forex" or s in ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]:
        if s == "XAUUSD":
            return "GC=F"
        return f"{s}=X"
    elif market_type == "crypto" or "USDT" in s:
        base = s.replace("USDT", "").replace("USD", "")
        return f"{base}-USD"
    elif "/" in symbol:
        base = s.replace("USD", "").replace("USDT", "")
        return f"{base}-USD"
    return symbol


_TICKER_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_TTL_SECONDS = 2.5


class MarketDataEngine:
    def __init__(self):
        self.cfg = get_settings()
        self._ccxt_exchanges: dict = {}

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        market_type: Literal["crypto", "forex", "stock"] = "crypto",
        exchange: str = "binance",
        limit: int = 300,
    ) -> pd.DataFrame:
        """Fetch OHLCV dataframe with automatic fallback chain."""
        tf = timeframe.lower()

        # 1. Try Primary Source
        try:
            if market_type == "crypto":
                df = await self._get_crypto(symbol, tf, exchange, limit)
                if not df.empty and len(df) >= 5:
                    return df
            elif market_type == "forex":
                df = await self._get_forex_mt5(symbol, tf, limit)
                if not df.empty and len(df) >= 5:
                    return df
            elif market_type == "stock":
                df = await self._get_yfinance(symbol, tf, market_type, limit)
                if not df.empty and len(df) >= 5:
                    return df
        except Exception as e:
            logger.warning(f"[MarketData] Primary fetch failed for {symbol} ({e}), trying yfinance fallback...")

        # 2. Fallback to Yahoo Finance
        try:
            df = await self._get_yfinance(symbol, tf, market_type, limit)
            if not df.empty and len(df) >= 5:
                return df
        except Exception as e:
            logger.warning(f"[MarketData] Yahoo Finance fallback failed for {symbol}: {e}")

        # 3. Final Fallback: Return empty dataframe safely (no fake data into strategy)
        logger.warning(f"[MarketData] All market data providers failed for {symbol}")
        return pd.DataFrame()

    async def get_ticker_24h(
        self,
        symbol: str,
        market_type: str = "crypto",
    ) -> dict:
        """Fetch exact 24h ticker with fast caching and non-blocking executors."""
        import time
        now = time.time()
        cache_key = f"{symbol}:{market_type}"

        # 1. Return from memory cache if fresh
        if cache_key in _TICKER_CACHE:
            ts, cached_data = _TICKER_CACHE[cache_key]
            if now - ts < CACHE_TTL_SECONDS:
                return cached_data

        import httpx
        clean_sym = symbol.replace("/", "").replace("-", "").upper()

        # 2. Crypto lookups (Binance / Bybit)
        if market_type == "crypto" or ("/" in symbol and "USD" in clean_sym and clean_sym not in ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]):
            binance_hosts = [
                "https://data-api.binance.vision",
                "https://api1.binance.com",
                "https://api.binance.com",
            ]
            for host in binance_hosts:
                try:
                    async with httpx.AsyncClient(timeout=1.2) as client:
                        resp = await client.get(f"{host}/api/v3/ticker/24hr", params={"symbol": clean_sym})
                        if resp.status_code == 200:
                            data = resp.json()
                            res = {
                                "symbol": symbol,
                                "price": float(data["lastPrice"]),
                                "change_24h": float(data["priceChangePercent"]),
                                "high_24h": float(data["highPrice"]),
                                "low_24h": float(data["lowPrice"]),
                                "volume_24h": float(data["volume"]),
                            }
                            _TICKER_CACHE[cache_key] = (now, res)
                            return res
                except Exception:
                    continue

        # 3. Non-blocking ThreadPool Executor for Yahoo Finance with strict 2.0s timeout
        def _get_yf_fast() -> dict:
            try:
                yf_sym = normalize_yfinance_symbol(symbol, market_type)
                t = yf.Ticker(yf_sym)
                last_p = 0.0
                prev_c = 0.0
                day_h = 0.0
                day_l = 0.0
                day_v = 0.0
                try:
                    fi = t.fast_info
                    last_p = float(getattr(fi, "last_price", None) or getattr(fi, "previous_close", None) or 0.0)
                    prev_c = float(getattr(fi, "previous_close", None) or last_p)
                    day_h = float(getattr(fi, "day_high", None) or (last_p * 1.01 if last_p > 0 else 0.0))
                    day_l = float(getattr(fi, "day_low", None) or (last_p * 0.99 if last_p > 0 else 0.0))
                    day_v = float(getattr(fi, "last_volume", None) or 100000.0)
                except Exception:
                    pass

                if last_p <= 0.0:
                    hist = t.history(period="2d", interval="5m")
                    if not hist.empty:
                        last_p = float(hist["Close"].iloc[-1])
                        prev_c = float(hist["Close"].iloc[0])
                        day_h = float(hist["High"].max())
                        day_l = float(hist["Low"].min())
                        day_v = float(hist["Volume"].sum())

                if last_p > 0.0:
                    chg = ((last_p - prev_c) / prev_c) * 100 if prev_c > 0 else 0.0
                    return {
                        "symbol": symbol,
                        "price": last_p,
                        "change_24h": round(chg, 2),
                        "high_24h": round(day_h, 2),
                        "low_24h": round(day_l, 2),
                        "volume_24h": round(day_v, 2),
                    }
            except Exception as e:
                logger.debug(f"[MarketData] YFinance fast ticker fallback for {symbol}: {e}")
            return {
                "symbol": symbol,
                "price": 0.0,
                "change_24h": 0.0,
                "high_24h": 0.0,
                "low_24h": 0.0,
                "volume_24h": 0.0,
            }

        try:
            loop = asyncio.get_running_loop()
            yf_res = await asyncio.wait_for(loop.run_in_executor(None, _get_yf_fast), timeout=2.0)
            if yf_res and yf_res.get("price", 0.0) > 0.0:
                _TICKER_CACHE[cache_key] = (now, yf_res)
                return yf_res
        except Exception as e:
            logger.debug(f"[MarketData] YF executor timeout/error for {symbol}: {e}")

        # 4. Fast Fallback for Forex & Gold via Binance Spot tokens (PAXGUSDT, EURUSDT, GBPUSDT)
        if clean_sym in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "GOLD"]:
            try:
                if clean_sym in ["XAUUSD", "GOLD"]:
                    spot_sym = "PAXGUSDT"
                elif clean_sym == "EURUSD":
                    spot_sym = "EURUSDT"
                elif clean_sym == "GBPUSD":
                    spot_sym = "GBPUSDT"
                else:
                    spot_sym = "USDCAD"
                async with httpx.AsyncClient(timeout=1.5) as client:
                    resp = await client.get("https://data-api.binance.vision/api/v3/ticker/24hr", params={"symbol": spot_sym})
                    if resp.status_code == 200:
                        data = resp.json()
                        res = {
                            "symbol": symbol,
                            "price": float(data["lastPrice"]),
                            "change_24h": float(data["priceChangePercent"]),
                            "high_24h": float(data["highPrice"]),
                            "low_24h": float(data["lowPrice"]),
                            "volume_24h": float(data["volume"]),
                        }
                        _TICKER_CACHE[cache_key] = (now, res)
                        return res
            except Exception:
                pass

        # 5. Return 0.0 on error without corrupting cache
        return {
            "symbol": symbol,
            "price": 0.0,
            "change_24h": 0.0,
            "high_24h": 0.0,
            "low_24h": 0.0,
            "volume_24h": 0.0,
        }

    async def _get_crypto(
        self, symbol: str, timeframe: str, exchange_name: str, limit: int
    ) -> pd.DataFrame:
        import httpx
        clean_sym = symbol.replace("/", "").replace("-", "").upper()
        tf = CCXT_TF_MAP.get(timeframe, "1h")

        binance_hosts = [
            "https://data-api.binance.vision",
            "https://api1.binance.com",
            "https://api.binance.com",
            "https://api2.binance.com",
        ]

        for host in binance_hosts:
            try:
                async with httpx.AsyncClient(timeout=3.5) as client:
                    resp = await client.get(
                        f"{host}/api/v3/klines",
                        params={"symbol": clean_sym, "interval": tf, "limit": limit},
                    )
                    if resp.status_code == 200:
                        raw = resp.json()
                        if raw and len(raw) >= 5:
                            # Binance kline format: [open_time, open, high, low, close, volume, ...]
                            df = pd.DataFrame(
                                raw,
                                columns=[
                                    "timestamp", "open", "high", "low", "close", "volume",
                                    "close_time", "qav", "trades", "tb_base", "tb_quote", "ignore"
                                ],
                            )
                            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                            df.set_index("timestamp", inplace=True)
                            for col in ["open", "high", "low", "close", "volume"]:
                                df[col] = pd.to_numeric(df[col], errors="coerce")
                            return df[["open", "high", "low", "close", "volume"]].dropna()
            except Exception as e:
                logger.debug(f"[MarketData] Host {host} failed: {e}")
                continue

        # Try Bybit Spot API
        try:
            bybit_tf_map = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D", "1w": "W"}
            b_tf = bybit_tf_map.get(tf, "60")
            async with httpx.AsyncClient(timeout=3.5) as client:
                resp = await client.get(
                    "https://api.bybit.com/v5/market/kline",
                    params={"category": "spot", "symbol": clean_sym, "interval": b_tf, "limit": limit},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    klist = data.get("result", {}).get("list", [])
                    if klist and len(klist) >= 5:
                        # Bybit returns reverse order [start_time, open, high, low, close, volume, ...]
                        df = pd.DataFrame(
                            klist,
                            columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
                        )
                        df["timestamp"] = pd.to_datetime(pd.to_numeric(df["timestamp"]), unit="ms", utc=True)
                        df.set_index("timestamp", inplace=True)
                        for col in ["open", "high", "low", "close", "volume"]:
                            df[col] = pd.to_numeric(df[col], errors="coerce")
                        df.sort_index(inplace=True)
                        return df[["open", "high", "low", "close", "volume"]].dropna()
        except Exception as e:
            logger.warning(f"[MarketData] Bybit fetch failed: {e}")

        raise ValueError(f"All crypto data sources failed for {symbol}")

    async def _get_yfinance(
        self, symbol: str, timeframe: str, market_type: str, limit: int
    ) -> pd.DataFrame:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_yfinance_sync, symbol, timeframe, market_type, limit)

    def _get_yfinance_sync(self, symbol: str, timeframe: str, market_type: str, limit: int) -> pd.DataFrame:
        yf_sym = normalize_yfinance_symbol(symbol, market_type)
        yf_tf = YF_TF_MAP.get(timeframe, "1h")

        # Period calculation
        period_map = {"1m": "7d", "2m": "7d", "5m": "60d", "15m": "60d", "30m": "60d",
                      "1h": "730d", "1d": "2y", "1wk": "5y", "1mo": "10y"}
        period = period_map.get(yf_tf, "1y")

        ticker = yf.Ticker(yf_sym)
        df = ticker.history(period=period, interval=yf_tf)
        if df.empty:
            return pd.DataFrame()

        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "timestamp"
        return df[["open", "high", "low", "close", "volume"]].tail(limit)

    async def _get_forex_mt5(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        return pd.DataFrame()  # Will fallback to yfinance for XAUUSD/Forex if MT5 is not connected

    def _generate_fallback_data(self, symbol: str, limit: int) -> pd.DataFrame:
        base_price = 64000.0 if "BTC" in symbol.upper() else 2400.0 if "XAU" in symbol.upper() else 180.0
        now = pd.Timestamp.now(tz="UTC")
        timestamps = [now - pd.Timedelta(hours=limit - i) for i in range(limit)]

        records = []
        c = base_price
        for _ in range(limit):
            delta = float(np.random.normal(0, base_price * 0.003))
            c = max(c + delta, 1.0)
            h = c + abs(float(np.random.normal(0, base_price * 0.002)))
            l = c - abs(float(np.random.normal(0, base_price * 0.002)))
            o = (records[-1]["close"] if records else c - 10.0)
            records.append({
                "open": o,
                "high": max(h, o, c),
                "low": min(l, o, c),
                "close": c,
                "volume": float(np.random.uniform(100, 1000)),
            })
            c = c

        df = pd.DataFrame(records, index=timestamps)
        df.index.name = "timestamp"
        return df
