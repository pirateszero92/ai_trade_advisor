"""
Market Data Engine
Fetches OHLCV data from multiple market sources:
  - Crypto  : ccxt (Binance, Bybit, etc.)
  - Forex   : MetaTrader 5 (via executor thread)
  - Stocks  : yfinance / Alpaca
"""

from __future__ import annotations

import asyncio
from typing import Literal, Optional

import pandas as pd
from loguru import logger

from app.core.config import get_settings


# ---------------------------------------------------------------------------
# Timeframe mappings
# ---------------------------------------------------------------------------

CCXT_TF_MAP: dict[str, str] = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1H": "1h", "2H": "2h", "4H": "4h", "6H": "6h", "8H": "8h", "12H": "12h",
    "1D": "1d", "3D": "3d", "1W": "1w", "1M": "1M",
}

YF_TF_MAP: dict[str, str] = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1H": "1h", "4H": "1h",  # yfinance doesn't have 4H, use 1H and resample
    "1D": "1d", "1W": "1wk", "1M": "1mo",
}


class MarketDataEngine:
    """
    Unified market data provider.

    Supports crypto (ccxt), forex/gold (MT5), and stocks (yfinance/Alpaca).
    All methods return a pandas DataFrame with a DatetimeIndex and standardised
    columns: open, high, low, close, volume.
    """

    def __init__(self):
        self.cfg = get_settings()
        self._ccxt_exchanges: dict = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1H",
        market_type: Literal["crypto", "forex", "stock"] = "crypto",
        exchange: str = "binance",
        limit: int = 500,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candles for the given symbol.

        Parameters
        ----------
        symbol:
            Instrument symbol.  For crypto use CCXT format e.g. ``"BTC/USDT"``.
            For stocks use ticker e.g. ``"AAPL"``.  For forex use MT5 format
            e.g. ``"XAUUSD"``.
        timeframe:
            Candle timeframe string: ``"1m"``, ``"15m"``, ``"1H"``, ``"4H"``,
            ``"1D"``, etc.
        market_type:
            One of ``"crypto"``, ``"forex"``, ``"stock"``.
        exchange:
            Exchange name for crypto (``"binance"`` or ``"bybit"``).
        limit:
            Number of candles to fetch.

        Returns
        -------
        pd.DataFrame
            DataFrame with DatetimeIndex and columns: open, high, low, close, volume.
            Returns an empty DataFrame on error.
        """
        try:
            if market_type == "crypto":
                return await self._get_crypto(symbol, timeframe, exchange, limit)
            elif market_type == "forex":
                return await self._get_forex_mt5(symbol, timeframe, limit)
            elif market_type == "stock":
                return await self._get_stock(symbol, timeframe, limit)
            else:
                logger.error(f"[MarketData] Unknown market_type: {market_type}")
                return pd.DataFrame()
        except Exception as exc:
            logger.exception(f"[MarketData] get_ohlcv failed for {symbol}: {exc}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Crypto — ccxt
    # ------------------------------------------------------------------

    async def _get_crypto(
        self, symbol: str, timeframe: str, exchange_name: str, limit: int
    ) -> pd.DataFrame:
        """Fetch OHLCV from a ccxt-compatible exchange (async)."""
        import ccxt.async_support as ccxt_async

        tf = CCXT_TF_MAP.get(timeframe, timeframe.lower())
        key = exchange_name.lower()

        if key not in self._ccxt_exchanges:
            ExchangeClass = getattr(ccxt_async, key, None)
            if ExchangeClass is None:
                raise ValueError(f"Unknown ccxt exchange: {exchange_name}")

            creds: dict = {}
            if key == "binance":
                creds = {
                    "apiKey": self.cfg.binance_api_key,
                    "secret": self.cfg.binance_api_secret,
                }
            elif key == "bybit":
                creds = {
                    "apiKey": self.cfg.bybit_api_key,
                    "secret": self.cfg.bybit_api_secret,
                }

            self._ccxt_exchanges[key] = ExchangeClass(
                {**creds, "enableRateLimit": True}
            )

        ex = self._ccxt_exchanges[key]
        raw = await ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        return df

    # ------------------------------------------------------------------
    # Forex / Gold — MetaTrader 5
    # ------------------------------------------------------------------

    async def _get_forex_mt5(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Fetch OHLCV from MetaTrader 5 using a thread executor (blocking call)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_mt5_sync, symbol, timeframe, limit)

    def _fetch_mt5_sync(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Synchronous MT5 fetch — runs in a thread pool."""
        try:
            import MetaTrader5 as mt5

            TF_MAP = {
                "1m": mt5.TIMEFRAME_M1,
                "5m": mt5.TIMEFRAME_M5,
                "15m": mt5.TIMEFRAME_M15,
                "30m": mt5.TIMEFRAME_M30,
                "1H": mt5.TIMEFRAME_H1,
                "4H": mt5.TIMEFRAME_H4,
                "1D": mt5.TIMEFRAME_D1,
                "1W": mt5.TIMEFRAME_W1,
            }
            mt5_tf = TF_MAP.get(timeframe, mt5.TIMEFRAME_H1)

            if not mt5.initialize(
                path=self.cfg.mt5_path,
                login=self.cfg.mt5_login,
                password=self.cfg.mt5_password,
                server=self.cfg.mt5_server,
            ):
                raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")

            rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, limit)
            mt5.shutdown()

            if rates is None or len(rates) == 0:
                return pd.DataFrame()

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            df.rename(columns={"time": "timestamp", "tick_volume": "volume"}, inplace=True)
            df.set_index("timestamp", inplace=True)
            return df[["open", "high", "low", "close", "volume"]]

        except ImportError:
            logger.warning("[MarketData] MetaTrader5 not installed — returning empty DataFrame")
            return pd.DataFrame()
        except Exception as exc:
            logger.error(f"[MarketData] MT5 fetch error: {exc}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Stocks — yfinance
    # ------------------------------------------------------------------

    async def _get_stock(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Fetch OHLCV from yfinance (runs in executor to avoid blocking)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_yf_sync, symbol, timeframe, limit)

    def _fetch_yf_sync(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Synchronous yfinance fetch."""
        try:
            import yfinance as yf

            period_map = {
                "1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d",
                "1H": "730d", "4H": "730d", "1D": "5y", "1W": "10y", "1M": "max",
            }
            tf = YF_TF_MAP.get(timeframe, "1d")
            period = period_map.get(timeframe, "1y")

            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=tf)
            if df.empty:
                return df
            df.columns = [c.lower() for c in df.columns]
            df.index = pd.to_datetime(df.index, utc=True)
            df.index.name = "timestamp"

            # Resample 1H -> 4H if needed
            if timeframe == "4H":
                df = df.resample("4h").agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
                ).dropna()

            return df[["open", "high", "low", "close", "volume"]].tail(limit)

        except Exception as exc:
            logger.error(f"[MarketData] yfinance error for {symbol}: {exc}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close any open ccxt exchange connections."""
        for ex in self._ccxt_exchanges.values():
            try:
                await ex.close()
            except Exception:
                pass
        self._ccxt_exchanges.clear()
