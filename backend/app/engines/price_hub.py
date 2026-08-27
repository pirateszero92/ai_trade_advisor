"""Central event-driven market Price Hub.

Binance Spot quotes, aggregate trades and closed candles arrive over a public
WebSocket. REST and Yahoo polling are retained strictly as labelled fallbacks
for disconnected crypto streams and markets without a configured streaming
provider.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import inspect
import math
import time
from typing import Callable, Optional

import httpx
from loguru import logger

from app.core.config import get_settings
from app.engines.realtime_market import (
    BinanceRealtimeClient,
    NormalizedMarketEvent,
    is_binance_spot_symbol,
    normalize_market_symbol,
)


import threading

class PriceHub:
    _instance: Optional["PriceHub"] = None

    def __new__(cls) -> "PriceHub":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._lock = threading.RLock()
        self._prices: dict[str, dict] = {}
        self._order_flow: dict[str, dict] = {}
        self._closed_candles: dict[tuple[str, str], OrderedDict[int, dict]] = {}
        self._live_candles: dict[tuple[str, str], dict] = {}
        self._subscribers: list[Callable[[dict], object]] = []
        self._active_symbols: set[str] = {
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
            "XAUUSD", "EURUSD", "GBPUSD", "USDJPY",
            "AAPL", "TSLA", "NVDA", "MSFT",
        }
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._stream_client: Optional[BinanceRealtimeClient] = None
        self._stale_after_seconds = 10.0
        self._fallback_stale_after_seconds = 45.0
        self._binance_rest_url = "https://data-api.binance.vision"
        self._max_closed_candles = 1500
        self._initialized = True
        logger.info("[PriceHub] Initialized event-driven in-memory market hub")

    @staticmethod
    def _normalize(symbol: str) -> str:
        return normalize_market_symbol(symbol)

    @staticmethod
    def _round_price(value: float) -> float:
        if value >= 1000:
            return round(value, 2)
        if value >= 1:
            return round(value, 6)
        return round(value, 10)

    def register_symbol(self, symbol: str) -> None:
        """Register a symbol and update the upstream subscription set."""
        clean_input = str(symbol).strip().upper()
        if not clean_input:
            return
        before = len(self._active_symbols)
        self._active_symbols.add(clean_input)
        if len(self._active_symbols) != before and self._stream_client:
            self._stream_client.refresh_subscriptions()

    def subscribe(self, callback: Callable[[dict], object]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[dict], object]) -> None:
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    @property
    def is_running(self) -> bool:
        fallback_running = self._task is not None and not self._task.done()
        stream_running = self._stream_client is None or self._stream_client.is_running
        return bool(self._running and fallback_running and stream_running)

    def _with_freshness(self, entry: dict, *, now: Optional[float] = None) -> dict:
        result = dict(entry)
        current = now if now is not None else time.time()
        received_at = float(result.get("received_timestamp", result.get("timestamp", 0.0)) or 0.0)
        age = max(0.0, current - received_at) if received_at > 0 else math.inf
        threshold = (
            self._stale_after_seconds
            if result.get("transport") == "websocket"
            else self._fallback_stale_after_seconds
        )
        result["age_seconds"] = round(age, 3) if math.isfinite(age) else None
        result["freshness"] = "fresh" if age <= threshold else "stale"
        result["is_stale"] = age > threshold
        return result

    def get_price(self, symbol: str, *, allow_stale: bool = True) -> Optional[float]:
        entry = self.get_ticker(symbol)
        if not entry or (entry.get("is_stale") and not allow_stale):
            return None
        price = float(entry.get("price", 0.0))
        return price if price > 0 else None

    def get_ticker(self, symbol: str) -> Optional[dict]:
        entry = self._prices.get(self._normalize(symbol))
        return self._with_freshness(entry) if entry else None

    def get_all_prices(self) -> dict[str, dict]:
        now = time.time()
        return {symbol: self._with_freshness(entry, now=now) for symbol, entry in self._prices.items()}

    def get_order_flow(self, symbol: str) -> dict:
        clean = self._normalize(symbol)
        flow = self._order_flow.get(clean)
        if not flow:
            return {
                "symbol": symbol,
                "norm_symbol": clean,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "volume_delta": 0.0,
                "delta_ratio": 0.0,
                "cvd": 0.0,
                "trade_count": 0,
                "source": "unavailable",
            }
        return dict(flow)

    def get_closed_candles(self, symbol: str, timeframe: str, *, limit: int = 300) -> list[dict]:
        if not 1 <= limit <= self._max_closed_candles:
            raise ValueError(f"limit must be between 1 and {self._max_closed_candles}")
        key = (self._normalize(symbol), timeframe)
        candles = self._closed_candles.get(key)
        if not candles:
            return []
        return [dict(value) for value in list(candles.values())[-limit:]]

    def update_price(
        self,
        symbol: str,
        price: float,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        change_24h: Optional[float] = None,
        high_24h: Optional[float] = None,
        low_24h: Optional[float] = None,
        volume_24h: Optional[float] = None,
        source: str = "stream",
        *,
        exchange_timestamp_ms: Optional[int] = None,
        received_timestamp_ms: Optional[int] = None,
        transport: Optional[str] = None,
        data_quality: Optional[str] = None,
        sequence: Optional[int] = None,
        extra: Optional[dict] = None,
    ) -> dict:
        """Update one quote and notify subscribers immediately."""
        if not math.isfinite(float(price)) or price <= 0:
            return {}
        clean = self._normalize(symbol)
        received_ms = int(received_timestamp_ms or time.time() * 1000)
        exchange_ms = int(exchange_timestamp_ms or received_ms)
        now = received_ms / 1000.0
        previous = self._prices.get(clean, {})
        previous_price = float(previous.get("price", price))
        effective_bid = bid if bid is not None else previous.get("bid")
        effective_ask = ask if ask is not None else previous.get("ask")
        if not effective_bid or float(effective_bid) <= 0:
            effective_bid = price * 0.9999
        if not effective_ask or float(effective_ask) <= 0:
            effective_ask = price * 1.0001

        effective_transport = transport or ("websocket" if source.endswith("_ws") else "rest_poll")
        effective_quality = data_quality or (
            "true_realtime" if effective_transport == "websocket" else "polling_fallback"
        )
        data = {
            "symbol": symbol,
            "norm_symbol": clean,
            "price": self._round_price(float(price)),
            "prev_price": previous_price,
            "tick_change": self._round_price(float(price) - previous_price),
            "bid": self._round_price(float(effective_bid)),
            "ask": self._round_price(float(effective_ask)),
            "change_24h": round(
                float(change_24h if change_24h is not None else previous.get("change_24h", 0.0)), 4
            ),
            "high_24h": self._round_price(float(
                high_24h if high_24h is not None else previous.get("high_24h", price)
            )),
            "low_24h": self._round_price(float(
                low_24h if low_24h is not None else previous.get("low_24h", price)
            )),
            "volume_24h": float(
                volume_24h if volume_24h is not None else previous.get("volume_24h", 0.0)
            ),
            "timestamp": now,
            "exchange_timestamp": exchange_ms / 1000.0,
            "received_timestamp": now,
            "latency_ms": max(0, received_ms - exchange_ms),
            "sequence": sequence,
            "source": source,
            "transport": effective_transport,
            "data_quality": effective_quality,
        }
        if extra:
            data.update(extra)
        self._prices[clean] = data
        self._notify(data)
        return self._with_freshness(data, now=now)

    def _notify(self, data: dict) -> None:
        for callback in tuple(self._subscribers):
            try:
                result = callback(dict(data))
                if inspect.isawaitable(result):
                    asyncio.create_task(result)
            except Exception as exc:
                logger.debug("[PriceHub] Subscriber failed: {}", exc)

    async def ingest_market_event(self, event: NormalizedMarketEvent) -> None:
        """Apply one normalized event to quote, order-flow and candle stores."""
        if event.event_type == "quote":
            self.update_price(
                event.symbol,
                float(event.price or 0),
                bid=event.bid,
                ask=event.ask,
                change_24h=event.change_24h,
                high_24h=event.high_24h,
                low_24h=event.low_24h,
                volume_24h=event.volume_24h,
                source=event.source,
                exchange_timestamp_ms=event.exchange_timestamp_ms,
                received_timestamp_ms=event.received_timestamp_ms,
                sequence=event.sequence,
                extra={
                    "bid_quantity": event.bid_quantity,
                    "ask_quantity": event.ask_quantity,
                },
            )
            return

        if event.event_type == "trade":
            clean = event.norm_symbol
            flow = self._order_flow.setdefault(clean, {
                "symbol": event.symbol,
                "norm_symbol": clean,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "volume_delta": 0.0,
                "delta_ratio": 0.0,
                "cvd": 0.0,
                "trade_count": 0,
                "source": "binance_aggressor_trade",
                "first_exchange_timestamp": event.exchange_timestamp_ms / 1000.0,
            })
            quantity = float(event.quantity or 0.0)
            signed_quantity = quantity if event.aggressor_side == "buy" else -quantity
            if signed_quantity >= 0:
                flow["buy_volume"] += quantity
            else:
                flow["sell_volume"] += quantity
            flow["volume_delta"] = flow["buy_volume"] - flow["sell_volume"]
            total = flow["buy_volume"] + flow["sell_volume"]
            flow["delta_ratio"] = flow["volume_delta"] / total if total > 0 else 0.0
            flow["cvd"] += signed_quantity
            flow["trade_count"] += 1
            flow["last_sequence"] = event.sequence
            flow["last_exchange_timestamp"] = event.exchange_timestamp_ms / 1000.0
            flow["last_received_timestamp"] = event.received_timestamp_ms / 1000.0
            flow["recovered_events"] = int(flow.get("recovered_events", 0)) + int(event.recovered)
            previous = self._prices.get(clean, {})
            self.update_price(
                event.symbol,
                float(event.price or 0),
                bid=previous.get("bid"),
                ask=previous.get("ask"),
                source=event.source,
                exchange_timestamp_ms=event.exchange_timestamp_ms,
                received_timestamp_ms=event.received_timestamp_ms,
                sequence=event.sequence,
                extra={
                    "aggressor_side": event.aggressor_side,
                    "last_trade_quantity": quantity,
                    "volume_delta": flow["volume_delta"],
                    "delta_ratio": flow["delta_ratio"],
                    "cvd": flow["cvd"],
                    "flow_source": flow["source"],
                },
            )
            return

        if event.event_type != "candle" or not event.timeframe:
            return
        key = (event.norm_symbol, event.timeframe)
        candle = {
            "symbol": event.symbol,
            "norm_symbol": event.norm_symbol,
            "timeframe": event.timeframe,
            "timestamp_ms": event.candle_open_time_ms,
            "close_timestamp_ms": event.candle_close_time_ms,
            "open": event.open,
            "high": event.high,
            "low": event.low,
            "close": event.close,
            "volume": event.volume,
            "buy_volume": event.buy_volume,
            "sell_volume": event.sell_volume,
            "volume_delta": event.volume_delta,
            "flow_source": "binance_taker_volume",
            "is_closed": event.is_closed,
            "source": event.source,
        }
        self._live_candles[key] = candle
        if event.is_closed and event.candle_open_time_ms is not None:
            candles = self._closed_candles.setdefault(key, OrderedDict())
            previous_cvd = next(reversed(candles.values())).get("cvd", 0.0) if candles else 0.0
            existing = candles.get(event.candle_open_time_ms)
            candle["cvd"] = (
                existing.get("cvd", previous_cvd + float(event.volume_delta or 0.0))
                if existing else previous_cvd + float(event.volume_delta or 0.0)
            )
            candles[event.candle_open_time_ms] = candle
            candles.move_to_end(event.candle_open_time_ms)
            while len(candles) > self._max_closed_candles:
                candles.popitem(last=False)

    def _crypto_symbols(self) -> set[str]:
        return {symbol for symbol in self._active_symbols if is_binance_spot_symbol(symbol)}

    async def start_stream(self) -> None:
        if self._running:
            return
        cfg = get_settings()
        self._stale_after_seconds = float(cfg.market_data_stale_after_seconds)
        self._fallback_stale_after_seconds = float(cfg.market_data_fallback_stale_after_seconds)
        intervals = tuple(
            value.strip() for value in cfg.binance_kline_intervals.split(",") if value.strip()
        )
        stream_client: Optional[BinanceRealtimeClient] = None
        if cfg.market_stream_enabled:
            stream_client = BinanceRealtimeClient(
                event_handler=self.ingest_market_event,
                symbols_provider=self._crypto_symbols,
                ws_url=cfg.binance_market_ws_url,
                rest_url=cfg.binance_market_rest_url,
                kline_intervals=intervals,
                max_symbols=cfg.market_stream_max_symbols,
                max_recovery_trades=cfg.market_stream_max_recovery_trades,
            )
        self._binance_rest_url = cfg.binance_market_rest_url.rstrip("/")
        self._stream_client = stream_client
        self._running = True
        if self._stream_client:
            await self._stream_client.start()
        self._task = asyncio.create_task(self._fallback_loop(), name="market-data-fallbacks")
        logger.info("[Phase4] Real-time market pipeline started")

    async def stop_stream(self) -> None:
        self._running = False
        if self._stream_client:
            await self._stream_client.stop()
            self._stream_client = None
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        logger.info("[Phase4] Real-time market pipeline stopped")

    async def _fallback_loop(self) -> None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=2.0))
        next_crypto = 0.0
        next_other = 0.0
        try:
            while self._running:
                try:
                    monotonic_now = time.monotonic()
                    jobs = []
                    stream_health = self._stream_client.health_snapshot(
                        stale_after_seconds=self._stale_after_seconds
                    ) if self._stream_client else {"fresh": False}
                    if monotonic_now >= next_crypto and not stream_health.get("fresh"):
                        jobs.append(self._fetch_crypto_fallback(client))
                        next_crypto = monotonic_now + 5.0
                    if monotonic_now >= next_other:
                        jobs.append(self._fetch_yahoo_fallback(client))
                        next_other = monotonic_now + 15.0
                    if jobs:
                        await asyncio.gather(*jobs)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.debug("[PriceHub] Fallback loop error: {}", exc)
                await asyncio.sleep(0.5)
        finally:
            await client.aclose()

    async def _fetch_crypto_fallback(self, client: httpx.AsyncClient) -> None:
        semaphore = asyncio.Semaphore(8)

        async def fetch(symbol: str) -> None:
            clean = self._normalize(symbol)
            async with semaphore:
                try:
                    response = await client.get(
                        f"{self._binance_rest_url}/api/v3/ticker/24hr",
                        params={"symbol": clean},
                        timeout=3.0,
                    )
                    if response.status_code != 200:
                        return
                    ticker = response.json()
                    price = float(ticker.get("lastPrice", 0))
                    if price <= 0:
                        return
                    existing = self.get_ticker(symbol)
                    if existing and existing.get("transport") == "websocket" and not existing.get("is_stale"):
                        return
                    now_ms = int(time.time() * 1000)
                    self.update_price(
                        symbol=symbol,
                        price=price,
                        bid=float(ticker.get("bidPrice", price)),
                        ask=float(ticker.get("askPrice", price)),
                        change_24h=float(ticker.get("priceChangePercent", 0)),
                        high_24h=float(ticker.get("highPrice", price)),
                        low_24h=float(ticker.get("lowPrice", price)),
                        volume_24h=float(ticker.get("volume", 0)),
                        source="binance_rest_fallback",
                        exchange_timestamp_ms=now_ms,
                        received_timestamp_ms=now_ms,
                        transport="rest_poll",
                        data_quality="polling_fallback",
                    )
                except Exception as exc:
                    logger.debug("[PriceHub] Binance fallback failed for {}: {}", symbol, exc)

        await asyncio.gather(*(fetch(symbol) for symbol in self._crypto_symbols()))

    async def _fetch_yahoo_fallback(self, client: httpx.AsyncClient) -> None:
        fx_map = {
            "XAUUSD": "GC=F",
            "EURUSD": "EURUSD=X",
            "GBPUSD": "GBPUSD=X",
            "USDJPY": "USDJPY=X",
            "AUDUSD": "AUDUSD=X",
            "USDCAD": "USDCAD=X",
            "USDTHB": "USDTHB=X",
        }
        requested: dict[str, str] = {"USDTHB": "USDTHB=X"}
        for symbol in tuple(self._active_symbols):
            if is_binance_spot_symbol(symbol):
                continue
            clean = self._normalize(symbol)
            requested[clean] = fx_map.get(clean, symbol.strip().upper())

        async def fetch(target_symbol: str, yahoo_symbol: str) -> None:
            try:
                response = await client.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}",
                    params={"interval": "1d", "range": "1d"},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=3.0,
                )
                response.raise_for_status()
                results = response.json().get("chart", {}).get("result") or []
                if not results:
                    return
                meta = results[0].get("meta", {})
                price = float(meta.get("regularMarketPrice") or 0)
                previous_close = float(meta.get("previousClose") or price)
                change = ((price - previous_close) / previous_close * 100.0) if previous_close > 0 else 0.0
                if price > 0:
                    now_ms = int(time.time() * 1000)
                    self.update_price(
                        target_symbol,
                        price,
                        change_24h=change,
                        source="yahoo_polling_fallback",
                        exchange_timestamp_ms=now_ms,
                        received_timestamp_ms=now_ms,
                        transport="rest_poll",
                        data_quality="delayed_polling_fallback",
                    )
            except Exception as exc:
                logger.debug("[PriceHub] Yahoo fallback failed for {}: {}", target_symbol, exc)

        await asyncio.gather(*(fetch(target, yahoo) for target, yahoo in requested.items()))

    def health_snapshot(self, *, now: Optional[float] = None) -> dict:
        current = now if now is not None else time.time()
        tickers = self.get_all_prices()
        symbols: dict[str, dict] = {}
        fresh_count = 0
        realtime_count = 0
        for requested_symbol in sorted(self._active_symbols):
            clean = self._normalize(requested_symbol)
            ticker = tickers.get(clean)
            if not ticker:
                symbols[clean] = {
                    "symbol": requested_symbol,
                    "status": "unavailable",
                    "data_quality": "unavailable",
                }
                continue
            status = "stale" if ticker["is_stale"] else "fresh"
            fresh_count += int(status == "fresh")
            realtime_count += int(ticker.get("data_quality") == "true_realtime" and status == "fresh")
            symbols[clean] = {
                "symbol": ticker.get("symbol", requested_symbol),
                "status": status,
                "age_seconds": ticker.get("age_seconds"),
                "source": ticker.get("source"),
                "transport": ticker.get("transport"),
                "data_quality": ticker.get("data_quality"),
                "latency_ms": ticker.get("latency_ms"),
            }
        provider = self._stream_client.health_snapshot(
            now=current, stale_after_seconds=self._stale_after_seconds
        ) if self._stream_client else {
            "provider": "binance", "transport": "websocket", "running": False,
            "connected": False, "fresh": False, "last_error": "disabled",
        }
        return {
            "phase": 4,
            "status": "healthy" if (
                provider.get("fresh") and provider.get("cvd_integrity") == "complete"
            ) else "degraded",
            "running": self.is_running,
            "true_realtime_scope": "Binance Spot symbols",
            "fallback_scope": "Forex, gold, equities, and disconnected crypto streams",
            "active_symbols": len(self._active_symbols),
            "fresh_symbols": fresh_count,
            "fresh_realtime_symbols": realtime_count,
            "providers": {"binance": provider},
            "symbols": symbols,
            "timestamp": current,
        }


price_hub = PriceHub()
