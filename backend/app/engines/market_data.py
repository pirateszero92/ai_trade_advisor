"""Market Data Engine with multi-source fallback (CCXT, MT5, yfinance)."""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Literal, Optional
import numpy as np
import pandas as pd
import yfinance as yf
import httpx
from loguru import logger

from app.core.config import get_settings

_YF_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="yf_worker")
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None


def get_shared_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            timeout=httpx.Timeout(4.0, connect=1.5),
        )
    return _HTTP_CLIENT


async def close_shared_http_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None and not _HTTP_CLIENT.is_closed:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


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


_USD_THB_CACHE: tuple[float, float] = (0.0, 34.0)


async def _get_usd_thb_rate_cached() -> float:
    global _USD_THB_CACHE
    import time
    now = time.time()
    last_time, rate = _USD_THB_CACHE
    if now - last_time < 120.0 and rate > 0:
        return rate
    try:
        client = get_shared_http_client()
        r = await client.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/USDTHB=X?interval=1d&range=1d",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=2.5,
        )
        if r.status_code == 200:
            data = r.json()
            new_rate = float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
            if new_rate > 20.0:
                _USD_THB_CACHE = (now, new_rate)
                return new_rate
    except Exception:
        pass
    return rate or 34.0


def normalize_yfinance_symbol(symbol: str, market_type: str) -> str:
    """Normalize any symbol format to Yahoo Finance ticker."""
    s = symbol.upper().replace("/", "").replace("-", "")

    if "THB" in s and market_type == "crypto":
        base = s.replace("THB", "")
        return f"{base}-USD"
    elif market_type == "forex" or s in ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]:
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
_OHLCV_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
CACHE_TTL_SECONDS = 5.0
CACHE_OHLCV_TTL = 8.0


def _prune_caches(now: float) -> None:
    """Keep in-memory cache bounded by evicting expired entries."""
    if len(_TICKER_CACHE) > 300:
        expired_ticker = [k for k, (ts, _) in _TICKER_CACHE.items() if now - ts > CACHE_TTL_SECONDS * 3]
        for k in expired_ticker:
            _TICKER_CACHE.pop(k, None)

    if len(_OHLCV_CACHE) > 300:
        expired_ohlcv = [k for k, (ts, _) in _OHLCV_CACHE.items() if now - ts > CACHE_OHLCV_TTL * 3]
        for k in expired_ohlcv:
            _OHLCV_CACHE.pop(k, None)


class MarketDataEngine:
    def __init__(self):
        self.cfg = get_settings()
        self._ccxt_exchanges: dict = {}

    async def get_usd_thb_rate(self) -> float:
        return await _get_usd_thb_rate_cached()

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        market_type: Literal["crypto", "forex", "stock"] = "crypto",
        exchange: str = "binance",
        limit: int = 300,
    ) -> pd.DataFrame:
        """Fetch OHLCV dataframe with automatic caching and fallback chain."""
        import time
        now = time.time()
        _prune_caches(now)
        tf = timeframe.lower()
        cache_key = f"{symbol}:{tf}:{market_type}:{limit}"

        if cache_key in _OHLCV_CACHE:
            ts, cached_df = _OHLCV_CACHE[cache_key]
            if now - ts < CACHE_OHLCV_TTL and not cached_df.empty:
                return cached_df.copy()

        df = pd.DataFrame()

        # 1. Try Primary Source
        try:
            if market_type == "crypto" or "THB" in symbol.upper():
                df = await self._get_crypto(symbol, tf, exchange, limit)
            elif market_type == "forex":
                df = await self._get_forex_mt5(symbol, tf, limit)
            elif market_type == "stock":
                df = await self._get_yfinance(symbol, tf, market_type, limit)
        except Exception as e:
            logger.warning(f"[MarketData] Primary fetch failed for {symbol} ({e}), trying yfinance fallback...")

        # 2. Fallback to Yahoo Finance
        if df.empty or len(df) < 5:
            try:
                df = await self._get_yfinance(symbol, tf, market_type, limit)
            except Exception as e:
                logger.warning(f"[MarketData] Yahoo Finance fallback failed for {symbol}: {e}")

        if not df.empty and len(df) >= 5:
            _OHLCV_CACHE[cache_key] = (now, df)
            return df

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
        _prune_caches(now)
        cache_key = f"{symbol}:{market_type}"

        # 1. Return from memory cache if fresh
        if cache_key in _TICKER_CACHE:
            ts, cached_data = _TICKER_CACHE[cache_key]
            if now - ts < CACHE_TTL_SECONDS:
                return cached_data

        client = get_shared_http_client()
        clean_sym = symbol.replace("/", "").replace("-", "").replace("_", "").upper()
        is_thb = "THB" in clean_sym
        if is_thb and (market_type == "crypto" or "THB" in symbol.upper()):
            base = clean_sym.replace("THB", "")
            query_sym = f"{base}USDT"
        else:
            query_sym = clean_sym

        # 1.5. If THB symbol (InnovestX Digital Asset), try InnovestX L2 Orderbook directly first
        if is_thb:
            try:
                from app.engines.innovestx_client import InnovestXClient
                ix_client = InnovestXClient()
                if ix_client.is_configured():
                    ix_data = await asyncio.wait_for(ix_client.get_live_ticker(symbol), timeout=1.8)
                    if ix_data and ix_data.get("price", 0) > 0:
                        p = float(ix_data["price"])
                        bid = float(ix_data.get("best_bid", p))
                        ask = float(ix_data.get("best_ask", p))
                        res = {
                            "symbol": symbol,
                            "price": p,
                            "change_24h": 0.0,
                            "high_24h": ask if ask > 0 else p,
                            "low_24h": bid if bid > 0 else p,
                            "volume_24h": 0.0,
                            "best_bid": bid,
                            "best_ask": ask,
                            "spread": float(ix_data.get("spread", 0.0)),
                            "exchange": "innovestx",
                        }
                        _TICKER_CACHE[cache_key] = (now, res)
                        return res
            except Exception as e:
                logger.debug(f"[MarketData] InnovestX direct ticker lookup: {e}")

        # 2. Crypto lookups (Binance / Bybit)
        if market_type == "crypto" or is_thb or ("/" in symbol and "USD" in clean_sym and clean_sym not in ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]):
            binance_hosts = [
                "https://api.binance.com",
                "https://api1.binance.com",
                "https://api2.binance.com",
                "https://api3.binance.com",
                "https://data-api.binance.vision",
            ]
            for host in binance_hosts:
                try:
                    resp = await client.get(f"{host}/api/v3/ticker/24hr", params={"symbol": query_sym}, timeout=2.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        p = float(data["lastPrice"])
                        h = float(data["highPrice"])
                        l = float(data["lowPrice"])
                        if is_thb:
                            rate = await _get_usd_thb_rate_cached()
                            p = round(p * rate, 4 if p * rate < 100 else 2)
                            h = round(h * rate, 4 if h * rate < 100 else 2)
                            l = round(l * rate, 4 if l * rate < 100 else 2)
                        res = {
                            "symbol": symbol,
                            "price": p,
                            "change_24h": float(data["priceChangePercent"]),
                            "high_24h": h,
                            "low_24h": l,
                            "volume_24h": float(data["volume"]),
                        }
                        _TICKER_CACHE[cache_key] = (now, res)
                        return res
                except Exception:
                    continue

        # 3. Fast Fallback for Forex & Gold via Binance Spot tokens (PAXGUSDT, EURUSDT, GBPUSDT)
        if clean_sym in ["EURUSD", "GBPUSD", "XAUUSD", "GOLD"]:
            try:
                if clean_sym in ["XAUUSD", "GOLD"]:
                    spot_sym = "PAXGUSDT"
                elif clean_sym == "EURUSD":
                    spot_sym = "EURUSDT"
                elif clean_sym == "GBPUSD":
                    spot_sym = "GBPUSDT"
                else:
                    spot_sym = None

                if spot_sym:
                    resp = await client.get("https://api.binance.com/api/v3/ticker/24hr", params={"symbol": spot_sym}, timeout=2.0)
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

        # 4. Non-blocking ThreadPool Executor for Yahoo Finance with strict 2.0s timeout
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
                    if is_thb:
                        rate = _USD_THB_CACHE[1] or 34.0
                        last_p = round(last_p * rate, 4 if last_p * rate < 100 else 2)
                        day_h = round(day_h * rate, 4 if day_h * rate < 100 else 2)
                        day_l = round(day_l * rate, 4 if day_l * rate < 100 else 2)
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
            yf_res = await asyncio.wait_for(loop.run_in_executor(_YF_EXECUTOR, _get_yf_fast), timeout=2.0)
            if yf_res and yf_res.get("price", 0.0) > 0.0:
                _TICKER_CACHE[cache_key] = (now, yf_res)
                return yf_res
        except Exception as e:
            logger.debug(f"[MarketData] YF executor timeout/error for {symbol}: {e}")

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
        clean_sym = symbol.replace("/", "").replace("-", "").replace("_", "").upper()
        is_thb = "THB" in clean_sym
        if is_thb:
            base = clean_sym.replace("THB", "")
            query_sym = f"{base}USDT"
        else:
            query_sym = clean_sym

        tf = CCXT_TF_MAP.get(timeframe, "1h")
        client = get_shared_http_client()

        binance_hosts = [
            "https://api.binance.com",
            "https://api1.binance.com",
            "https://api2.binance.com",
            "https://api3.binance.com",
            "https://data-api.binance.vision",
        ]

        df = pd.DataFrame()
        for host in binance_hosts:
            try:
                resp = await client.get(
                    f"{host}/api/v3/klines",
                    params={"symbol": query_sym, "interval": tf, "limit": limit},
                    timeout=2.5,
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
                        df = df[["open", "high", "low", "close", "volume"]].dropna()
                        break
            except Exception as e:
                logger.debug(f"[MarketData] Host {host} failed: {e}")
                continue

        # Try Bybit Spot API
        if df.empty:
            try:
                bybit_tf_map = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D", "1w": "W"}
                b_tf = bybit_tf_map.get(tf, "60")
                async with httpx.AsyncClient(timeout=3.5) as client:
                    resp = await client.get(
                        "https://api.bybit.com/v5/market/kline",
                        params={"category": "spot", "symbol": query_sym, "interval": b_tf, "limit": limit},
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
                            df = df[["open", "high", "low", "close", "volume"]].dropna()
            except Exception as e:
                logger.warning(f"[MarketData] Bybit fetch failed: {e}")

        if not df.empty:
            if is_thb:
                usd_thb = await _get_usd_thb_rate_cached()
                # If InnovestX is configured, calibrate candle price to exact InnovestX orderbook price
                try:
                    from app.engines.innovestx_client import InnovestXClient
                    ix_client = InnovestXClient()
                    if ix_client.is_configured():
                        ix_data = await asyncio.wait_for(ix_client.get_live_ticker(symbol), timeout=1.5)
                        if ix_data and ix_data.get("price", 0) > 0:
                            last_raw = float(df["close"].iloc[-1])
                            if last_raw > 0:
                                usd_thb = float(ix_data["price"]) / last_raw
                except Exception:
                    pass

                for col in ["open", "high", "low", "close"]:
                    df[col] = df[col] * usd_thb
                last_c = df["close"].iloc[-1]
                dec = 4 if last_c < 100 else 2
                for col in ["open", "high", "low", "close"]:
                    df[col] = df[col].round(dec)
            return df

        raise ValueError(f"All crypto data sources failed for {symbol}")

    async def _get_yfinance(
        self, symbol: str, timeframe: str, market_type: str, limit: int
    ) -> pd.DataFrame:
        try:
            loop = asyncio.get_running_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(_YF_EXECUTOR, self._get_yfinance_sync, symbol, timeframe, market_type, limit),
                timeout=4.5,
            )
        except Exception as e:
            logger.debug(f"[MarketData] YFinance fetch timeout/error for {symbol}: {e}")
            return pd.DataFrame()

    def _get_yfinance_sync(self, symbol: str, timeframe: str, market_type: str, limit: int) -> pd.DataFrame:
        clean_sym = symbol.replace("/", "").replace("-", "").replace("_", "").upper()
        is_thb = "THB" in clean_sym and (market_type == "crypto" or "THB" in symbol.upper())
        yf_sym = normalize_yfinance_symbol(symbol, market_type)
        is_4h = timeframe.lower() == "4h"
        yf_tf = "1h" if is_4h else YF_TF_MAP.get(timeframe, "1h")

        # Period calculation - limit to 60d for intraday to prevent huge downloads
        period_map = {"1m": "7d", "2m": "7d", "5m": "30d", "15m": "30d", "30m": "30d",
                      "1h": "60d", "1d": "1y", "1wk": "2y", "1mo": "5y"}
        period = period_map.get(yf_tf, "60d")

        ticker = yf.Ticker(yf_sym)
        fetch_limit = (limit * 4) if is_4h else limit
        df = ticker.history(period=period, interval=yf_tf)
        if df.empty:
            return pd.DataFrame()

        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "timestamp"

        if is_4h:
            # Resample 1h bars to clean 4h bars
            df_4h = df[["open", "high", "low", "close", "volume"]].resample("4h").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna()
            res_df = df_4h.tail(limit)
        else:
            res_df = df[["open", "high", "low", "close", "volume"]].tail(limit)

        if is_thb and not res_df.empty:
            rate = _USD_THB_CACHE[1] or 34.0
            for col in ["open", "high", "low", "close"]:
                res_df[col] = res_df[col] * rate
            last_c = res_df["close"].iloc[-1]
            dec = 4 if last_c < 100 else 2
            for col in ["open", "high", "low", "close"]:
                res_df[col] = res_df[col].round(dec)
        return res_df

    async def _get_forex_mt5(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Fast Forex & Gold kline fetch via Binance Spot liquidity tokens."""
        clean = symbol.upper().replace("/", "").replace("-", "")
        if clean in ["XAUUSD", "GOLD"]:
            spot_sym = "PAXGUSDT"
        elif clean == "EURUSD":
            spot_sym = "EURUSDT"
        elif clean == "GBPUSD":
            spot_sym = "GBPUSDT"
        else:
            spot_sym = None

        if spot_sym:
            try:
                df = await self._get_crypto(spot_sym, timeframe, "binance", limit)
                if not df.empty and len(df) >= 5:
                    return df
            except Exception:
                pass
        return pd.DataFrame()

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
