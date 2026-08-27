"""Market Data Engine with multi-source fallback (CCXT, MT5, yfinance)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Literal, Optional
import pandas as pd
import yfinance as yf
import httpx
from loguru import logger

from app.core.config import get_settings

_YF_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="yf_worker")
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_HTTP_CLIENT_LOCK = threading.Lock()


def get_shared_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
                _HTTP_CLIENT = httpx.AsyncClient(
                    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
                    timeout=httpx.Timeout(4.0, connect=1.5),
                )
    return _HTTP_CLIENT


async def close_shared_http_client() -> None:
    global _HTTP_CLIENT
    with _HTTP_CLIENT_LOCK:
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

SUPPORTED_TIMEFRAMES = {"1m", "2m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"}

_FIXED_TIMEFRAME_SECONDS = {
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}


def canonical_timeframe(value: str) -> str:
    timeframe = "1M" if value == "1M" else str(value).lower()
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: {value}")
    return timeframe


def closed_candle_frame(
    frame: pd.DataFrame,
    timeframe: str,
    *,
    limit: int | None = None,
    now: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Return only candles whose full interval has elapsed.

    Provider APIs do not agree on whether their final row is still forming.
    Strategy consumers therefore use this provider-independent finalizer so a
    scan and a chart decision can never be based on a repainting candle.
    Candle indexes are treated as interval-open timestamps, matching Binance,
    Bybit and yfinance.
    """
    if frame.empty:
        return frame.copy()
    tf = canonical_timeframe(timeframe)
    current = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    else:
        current = current.tz_convert("UTC")

    result = frame.copy()
    index = pd.to_datetime(result.index, utc=True, errors="coerce")
    valid_index = ~index.isna()
    if tf == "1M":
        close_times = index + pd.offsets.MonthBegin(1)
    else:
        close_times = index + pd.to_timedelta(_FIXED_TIMEFRAME_SECONDS[tf], unit="s")
    result = result.loc[valid_index & (close_times <= current)]
    result.index = index[valid_index & (close_times <= current)]
    result.index.name = frame.index.name or "timestamp"
    result = result[~result.index.duplicated(keep="last")].sort_index()
    return result.tail(limit) if limit is not None else result


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
        *,
        closed_only: bool = False,
    ) -> pd.DataFrame:
        """Fetch OHLCV dataframe with automatic caching and fallback chain."""
        import time
        now = time.time()
        _prune_caches(now)
        if not symbol or len(symbol) > 30 or limit < 5 or limit > 1500:
            raise ValueError("Invalid symbol or OHLCV limit")
        tf = canonical_timeframe(timeframe)
        if market_type == "crypto":
            try:
                from app.engines.price_hub import price_hub
                price_hub.register_symbol(symbol)
            except Exception as exc:
                logger.debug(f"[MarketData] Stream registration skipped for {symbol}: {exc}")
        # Fetch one additional provider row when a closed snapshot is needed;
        # many APIs include the currently-forming candle in their limit.
        fetch_limit = min(1500, limit + 1) if closed_only else limit
        cache_key = f"{symbol}:{tf}:{market_type}:{exchange}:{fetch_limit}"

        def finalize(candidate: pd.DataFrame) -> pd.DataFrame:
            merged = self._merge_realtime_closed_candles(
                candidate.copy(), symbol, tf, market_type, fetch_limit
            )
            if closed_only:
                return closed_candle_frame(merged, tf, limit=limit)
            return merged.tail(limit)

        if cache_key in _OHLCV_CACHE:
            ts, cached_df = _OHLCV_CACHE[cache_key]
            if now - ts < CACHE_OHLCV_TTL and not cached_df.empty:
                return finalize(cached_df)

        df = pd.DataFrame()

        # 1. Try Primary Source
        try:
            if market_type == "crypto":
                df = await self._get_crypto(symbol, tf, exchange, fetch_limit)
            elif market_type == "forex":
                # Currency and metals analysis must use the requested market,
                # not a similarly-priced crypto token proxy.
                df = await self._get_yfinance(symbol, tf, market_type, fetch_limit)
            elif market_type == "stock":
                df = await self._get_yfinance(symbol, tf, market_type, fetch_limit)
        except Exception as e:
            logger.warning(f"[MarketData] Primary fetch failed for {symbol} ({e}), trying yfinance fallback...")

        # 2. Fallback to Yahoo Finance
        if df.empty or len(df) < 5:
            try:
                df = await self._get_yfinance(symbol, tf, market_type, fetch_limit)
            except Exception as e:
                logger.warning(f"[MarketData] Yahoo Finance fallback failed for {symbol}: {e}")

        if not df.empty and len(df) >= 5:
            _OHLCV_CACHE[cache_key] = (now, df)
            return finalize(df)

        # 3. Final Fallback: Return empty dataframe safely (no fake data into strategy)
        logger.warning(f"[MarketData] All market data providers failed for {symbol}")
        return pd.DataFrame()

    @staticmethod
    def _merge_realtime_closed_candles(
        frame: pd.DataFrame,
        symbol: str,
        timeframe: str,
        market_type: str,
        limit: int,
    ) -> pd.DataFrame:
        """Overlay only exchange-confirmed closed candles from the live hub."""
        if market_type != "crypto" or frame.empty:
            return frame
        try:
            from app.engines.price_hub import price_hub

            rows = price_hub.get_closed_candles(symbol, timeframe, limit=min(limit, 300))
            if not rows:
                return frame
            live = pd.DataFrame(rows)
            live["timestamp"] = pd.to_datetime(live["timestamp_ms"], unit="ms", utc=True)
            live.set_index("timestamp", inplace=True)
            columns = [
                "open", "high", "low", "close", "volume", "buy_volume",
                "sell_volume", "volume_delta", "cvd", "flow_source",
            ]
            live = live[[column for column in columns if column in live.columns]]
            numeric_columns = [column for column in columns[:-1] if column in live.columns]
            live[numeric_columns] = live[numeric_columns].apply(pd.to_numeric, errors="coerce")
            live = live.dropna(subset=["open", "high", "low", "close", "volume"])
            combined = pd.concat([frame, live], axis=0, sort=False)
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            return combined.tail(limit)
        except Exception as exc:
            logger.debug(f"[MarketData] Live closed-candle merge skipped for {symbol}: {exc}")
            return frame

    async def get_ticker_24h(
        self,
        symbol: str,
        market_type: str = "crypto",
    ) -> dict:
        """Fetch exact 24h ticker with fast caching and non-blocking executors."""
        import time
        now = time.time()
        _prune_caches(now)
        if not symbol or len(symbol) > 30 or market_type not in {"crypto", "forex", "stock"}:
            raise ValueError("Invalid ticker request")
        cache_key = f"{symbol}:{market_type}"

        # Phase 4: all consumers share the event-driven Price Hub first. REST
        # providers are used only when the stream/fallback cache is unavailable
        # or stale, eliminating duplicate quote polling across API endpoints.
        try:
            from app.engines.price_hub import price_hub

            price_hub.register_symbol(symbol)
            hub_ticker = price_hub.get_ticker(symbol)
            if hub_ticker and not hub_ticker.get("is_stale") and hub_ticker.get("price", 0) > 0:
                bid = float(hub_ticker.get("bid") or hub_ticker["price"])
                ask = float(hub_ticker.get("ask") or hub_ticker["price"])
                return {
                    "symbol": symbol,
                    "price": float(hub_ticker["price"]),
                    "change_24h": float(hub_ticker.get("change_24h", 0.0)),
                    "high_24h": float(hub_ticker.get("high_24h", hub_ticker["price"])),
                    "low_24h": float(hub_ticker.get("low_24h", hub_ticker["price"])),
                    "volume_24h": float(hub_ticker.get("volume_24h", 0.0)),
                    "best_bid": bid,
                    "best_ask": ask,
                    "spread": max(0.0, ask - bid),
                    "exchange": hub_ticker.get("source", "price_hub"),
                    "transport": hub_ticker.get("transport"),
                    "data_quality": hub_ticker.get("data_quality"),
                    "latency_ms": hub_ticker.get("latency_ms"),
                    "freshness": hub_ticker.get("freshness"),
                }
        except Exception as exc:
            logger.debug(f"[MarketData] Price Hub lookup skipped for {symbol}: {exc}")

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
        if market_type == "crypto":
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

        # 3. Non-blocking ThreadPool Executor for Yahoo Finance with strict timeout
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
                    if is_thb:
                        rate = _USD_THB_CACHE[1] or 34.0
                        last_p = round(last_p * rate, 4 if last_p * rate < 100 else 2)
                        day_h = round(day_h * rate, 4 if day_h * rate < 100 else 2)
                        day_l = round(day_l * rate, 4 if day_l * rate < 100 else 2)
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

        # Return an explicit unavailable quote without corrupting cache.
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

        tf = canonical_timeframe(timeframe)
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
                        # Exclude the currently-forming candle to prevent
                        # indicator repainting and false structure breaks.
                        now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
                        df = df[pd.to_numeric(df["close_time"], errors="coerce") <= now_ms]
                        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                        df.set_index("timestamp", inplace=True)
                        for col in ["open", "high", "low", "close", "volume", "tb_base"]:
                            df[col] = pd.to_numeric(df[col], errors="coerce")
                        # Binance provides taker-buy base volume. The remainder
                        # is taker-sell volume, yielding exchange-derived
                        # aggressor delta instead of a candle-shape estimate.
                        df["buy_volume"] = df["tb_base"].clip(lower=0)
                        df["sell_volume"] = (df["volume"] - df["buy_volume"]).clip(lower=0)
                        df["volume_delta"] = df["buy_volume"] - df["sell_volume"]
                        df["cvd"] = df["volume_delta"].cumsum()
                        df["flow_source"] = "binance_taker_volume"
                        df = df[[
                            "open", "high", "low", "close", "volume",
                            "buy_volume", "sell_volume", "volume_delta", "cvd",
                            "flow_source",
                        ]].dropna(subset=["open", "high", "low", "close", "volume"])
                        break
            except Exception as e:
                logger.debug(f"[MarketData] Host {host} failed: {e}")
                continue

        # Try Bybit Spot API
        if df.empty:
            try:
                bybit_tf_map = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "1d": "D", "1w": "W", "1M": "M"}
                b_tf = bybit_tf_map.get(tf)
                if b_tf is None:
                    raise ValueError(f"Bybit does not support timeframe {tf}")
                shared_client = get_shared_http_client()
                resp = await shared_client.get(
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
        timeframe = canonical_timeframe(timeframe)
        is_4h = timeframe == "4h"
        yf_tf = "1h" if is_4h else YF_TF_MAP.get(timeframe)
        if yf_tf is None:
            raise ValueError(f"Yahoo Finance does not support timeframe {timeframe}")

        # Period calculation - limit to 60d for intraday to prevent huge downloads
        period_map = {"1m": "7d", "2m": "7d", "5m": "30d", "15m": "30d", "30m": "30d",
                      "1h": "60d", "1d": "1y", "1wk": "2y", "1mo": "5y"}
        period = period_map.get(yf_tf, "60d")

        ticker = yf.Ticker(yf_sym)
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
            res_df = df_4h.tail(limit).copy()
        else:
            res_df = df[["open", "high", "low", "close", "volume"]].tail(limit).copy()

        if is_thb and not res_df.empty:
            rate = _USD_THB_CACHE[1] or 34.0
            for col in ["open", "high", "low", "close"]:
                res_df[col] = res_df[col] * rate
            last_c = res_df["close"].iloc[-1]
            dec = 4 if last_c < 100 else 2
            for col in ["open", "high", "low", "close"]:
                res_df[col] = res_df[col].round(dec)
        return res_df
