"""Normalized real-time market events and Binance Spot WebSocket adapter.

The adapter consumes public market data only. It cannot authenticate, submit
orders, or cross the Paper/Live execution boundary.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
import math
import random
import time
from typing import Awaitable, Callable, Literal, Optional

import httpx
from loguru import logger
import websockets


EventType = Literal["quote", "trade", "candle"]
AggressorSide = Literal["buy", "sell"]

BINANCE_QUOTE_ASSETS = ("FDUSD", "USDT", "USDC", "BTC", "ETH", "BNB")
BINANCE_KLINE_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h",
    "12h", "1d", "3d", "1w", "1M",
}


def normalize_market_symbol(symbol: str) -> str:
    return str(symbol).replace("/", "").replace("-", "").replace("_", "").upper()


def display_binance_symbol(symbol: str) -> str:
    clean = normalize_market_symbol(symbol)
    for quote in BINANCE_QUOTE_ASSETS:
        if clean.endswith(quote) and len(clean) > len(quote):
            return f"{clean[:-len(quote)]}/{quote}"
    return clean


def is_binance_spot_symbol(symbol: str) -> bool:
    clean = normalize_market_symbol(symbol)
    return any(clean.endswith(quote) and len(clean) > len(quote) for quote in BINANCE_QUOTE_ASSETS)


@dataclass(frozen=True)
class NormalizedMarketEvent:
    event_type: EventType
    symbol: str
    source: str
    exchange_timestamp_ms: int
    received_timestamp_ms: int
    sequence: Optional[int] = None
    price: Optional[float] = None
    quantity: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_quantity: Optional[float] = None
    ask_quantity: Optional[float] = None
    change_24h: Optional[float] = None
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    volume_24h: Optional[float] = None
    aggressor_side: Optional[AggressorSide] = None
    timeframe: Optional[str] = None
    candle_open_time_ms: Optional[int] = None
    candle_close_time_ms: Optional[int] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None
    buy_volume: Optional[float] = None
    sell_volume: Optional[float] = None
    volume_delta: Optional[float] = None
    is_closed: bool = False
    recovered: bool = False

    @property
    def norm_symbol(self) -> str:
        return normalize_market_symbol(self.symbol)

    @property
    def latency_ms(self) -> int:
        return max(0, self.received_timestamp_ms - self.exchange_timestamp_ms)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["norm_symbol"] = self.norm_symbol
        payload["latency_ms"] = self.latency_ms
        return payload


def _finite_float(value: object, *, field: str, allow_zero: bool = True) -> float:
    number = float(value)
    if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
        raise ValueError(f"Invalid {field}")
    return number


def parse_binance_message(
    message: str | bytes | dict,
    *,
    received_timestamp_ms: Optional[int] = None,
    recovered: bool = False,
) -> Optional[NormalizedMarketEvent]:
    """Parse Binance raw/combined-stream frames into one canonical event."""
    if isinstance(message, (str, bytes)):
        payload = json.loads(message)
    else:
        payload = message
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    if not isinstance(data, dict) or "result" in data:
        return None

    received_ms = int(received_timestamp_ms or time.time() * 1000)
    event_name = data.get("e")
    symbol = display_binance_symbol(str(data.get("s", "")))
    if not symbol:
        return None

    if event_name == "aggTrade" or (
        "a" in data and "p" in data and "q" in data and "m" in data
    ):
        price = _finite_float(data["p"], field="trade price", allow_zero=False)
        quantity = _finite_float(data["q"], field="trade quantity", allow_zero=False)
        # E is the exchange event timestamp and therefore the correct transport
        # latency anchor. T remains the underlying trade timestamp and is used
        # by REST recovery payloads, which do not include E.
        exchange_ms = int(data.get("E") or data.get("T") or received_ms)
        return NormalizedMarketEvent(
            event_type="trade",
            symbol=symbol,
            source="binance_rest_recovery" if recovered else "binance_ws",
            exchange_timestamp_ms=exchange_ms,
            received_timestamp_ms=received_ms,
            sequence=int(data["a"]),
            price=price,
            quantity=quantity,
            aggressor_side="sell" if bool(data["m"]) else "buy",
            recovered=recovered,
        )

    if event_name == "kline" and isinstance(data.get("k"), dict):
        kline = data["k"]
        volume = _finite_float(kline["v"], field="kline volume")
        buy_volume = min(volume, _finite_float(kline.get("V", 0), field="taker buy volume"))
        sell_volume = max(0.0, volume - buy_volume)
        exchange_ms = int(data.get("E") or kline.get("T") or received_ms)
        return NormalizedMarketEvent(
            event_type="candle",
            symbol=symbol,
            source="binance_ws",
            exchange_timestamp_ms=exchange_ms,
            received_timestamp_ms=received_ms,
            price=_finite_float(kline["c"], field="close price", allow_zero=False),
            timeframe=str(kline["i"]),
            candle_open_time_ms=int(kline["t"]),
            candle_close_time_ms=int(kline["T"]),
            open=_finite_float(kline["o"], field="open", allow_zero=False),
            high=_finite_float(kline["h"], field="high", allow_zero=False),
            low=_finite_float(kline["l"], field="low", allow_zero=False),
            close=_finite_float(kline["c"], field="close", allow_zero=False),
            volume=volume,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            volume_delta=buy_volume - sell_volume,
            is_closed=bool(kline.get("x")),
        )

    if event_name == "24hrTicker":
        price = _finite_float(data["c"], field="ticker price", allow_zero=False)
        return NormalizedMarketEvent(
            event_type="quote",
            symbol=symbol,
            source="binance_ws",
            exchange_timestamp_ms=int(data.get("E") or received_ms),
            received_timestamp_ms=received_ms,
            price=price,
            bid=_finite_float(data.get("b", price), field="bid", allow_zero=False),
            ask=_finite_float(data.get("a", price), field="ask", allow_zero=False),
            bid_quantity=_finite_float(data.get("B", 0), field="bid quantity"),
            ask_quantity=_finite_float(data.get("A", 0), field="ask quantity"),
            change_24h=float(data.get("P", 0)),
            high_24h=_finite_float(data.get("h", price), field="24h high", allow_zero=False),
            low_24h=_finite_float(data.get("l", price), field="24h low", allow_zero=False),
            volume_24h=_finite_float(data.get("v", 0), field="24h volume"),
        )

    # bookTicker has no `e` or exchange event timestamp. Its update ID is not
    # guaranteed to be consecutive when only best-book prices change, so it is
    # recorded for traceability but never used for trade-gap detection.
    if {"u", "b", "a"}.issubset(data):
        bid = _finite_float(data["b"], field="bid", allow_zero=False)
        ask = _finite_float(data["a"], field="ask", allow_zero=False)
        return NormalizedMarketEvent(
            event_type="quote",
            symbol=symbol,
            source="binance_ws",
            exchange_timestamp_ms=int(data.get("E") or received_ms),
            received_timestamp_ms=received_ms,
            sequence=int(data["u"]),
            price=(bid + ask) / 2.0,
            bid=bid,
            ask=ask,
            bid_quantity=_finite_float(data.get("B", 0), field="bid quantity"),
            ask_quantity=_finite_float(data.get("A", 0), field="ask quantity"),
        )
    return None


@dataclass(frozen=True)
class SequenceObservation:
    accepted: bool
    duplicate: bool = False
    gap_start: Optional[int] = None
    gap_end: Optional[int] = None


class TradeSequenceTracker:
    """Tracks aggregate-trade IDs without applying order-book semantics."""

    def __init__(self) -> None:
        self._last: dict[str, int] = {}

    def observe(self, symbol: str, sequence: int) -> SequenceObservation:
        clean = normalize_market_symbol(symbol)
        previous = self._last.get(clean)
        if previous is not None and sequence <= previous:
            return SequenceObservation(accepted=False, duplicate=True)
        self._last[clean] = sequence
        if previous is not None and sequence > previous + 1:
            return SequenceObservation(
                accepted=True,
                gap_start=previous + 1,
                gap_end=sequence - 1,
            )
        return SequenceObservation(accepted=True)

    def last(self, symbol: str) -> Optional[int]:
        return self._last.get(normalize_market_symbol(symbol))


class BinanceRealtimeClient:
    """Resilient Binance public stream with aggregate-trade gap recovery."""

    def __init__(
        self,
        *,
        event_handler: Callable[[NormalizedMarketEvent], Awaitable[None] | None],
        symbols_provider: Callable[[], set[str]],
        ws_url: str = "wss://stream.binance.com:9443/stream",
        rest_url: str = "https://data-api.binance.vision",
        kline_intervals: tuple[str, ...] = ("1m", "15m", "1h", "4h", "1d"),
        max_symbols: int = 100,
        max_recovery_trades: int = 5000,
    ) -> None:
        invalid_intervals = set(kline_intervals) - BINANCE_KLINE_INTERVALS
        if invalid_intervals:
            raise ValueError(f"Unsupported Binance kline intervals: {sorted(invalid_intervals)}")
        if not 1 <= max_symbols <= 100:
            raise ValueError("max_symbols must be between 1 and 100")
        if not 1 <= max_recovery_trades <= 10_000:
            raise ValueError("max_recovery_trades must be between 1 and 10000")
        self.event_handler = event_handler
        self.symbols_provider = symbols_provider
        self.ws_url = ws_url.rstrip("/")
        self.rest_url = rest_url.rstrip("/")
        self.kline_intervals = tuple(kline_intervals)
        self.max_symbols = max_symbols
        self.max_recovery_trades = max_recovery_trades
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._subscription_changed = asyncio.Event()
        self._sequence = TradeSequenceTracker()
        self.connected = False
        self.connected_at: Optional[float] = None
        self.last_event_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.reconnect_count = 0
        self.events_received = 0
        self.duplicates_dropped = 0
        self.sequence_gap_count = 0
        self.recovered_events = 0
        self.unrecoverable_gaps = 0
        self.last_trade_latency_ms: Optional[int] = None
        self.trade_latency_ema_ms: Optional[float] = None
        self.max_trade_latency_ms = 0
        self.subscribed_streams = 0
        self.skipped_symbols: list[str] = []

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="binance-market-stream")

    async def stop(self) -> None:
        self._running = False
        self._subscription_changed.set()
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self.connected = False

    def refresh_subscriptions(self) -> None:
        self._subscription_changed.set()

    def _desired_streams(self) -> set[str]:
        candidates = sorted({
            normalize_market_symbol(symbol)
            for symbol in self.symbols_provider()
            if is_binance_spot_symbol(symbol)
        })
        selected = candidates[:self.max_symbols]
        self.skipped_symbols = candidates[self.max_symbols:]
        streams: set[str] = set()
        for symbol in selected:
            lower = symbol.lower()
            streams.update({f"{lower}@aggTrade", f"{lower}@bookTicker", f"{lower}@ticker"})
            streams.update(f"{lower}@kline_{interval}" for interval in self.kline_intervals)
        return streams

    async def _emit(self, event: NormalizedMarketEvent) -> None:
        result = self.event_handler(event)
        if asyncio.iscoroutine(result):
            await result

    async def _sync_subscriptions(self, websocket, current: set[str]) -> None:
        request_id = 1
        last_control_message_at = 0.0

        async def send_control(method: str, params: list[str]) -> None:
            nonlocal request_id, last_control_message_at
            # Binance counts SUBSCRIBE/UNSUBSCRIBE and protocol ping/pong
            # frames against its inbound-message limit. Keep application
            # control frames below that limit even during watchlist bursts.
            elapsed = time.monotonic() - last_control_message_at
            if elapsed < 0.30:
                await asyncio.sleep(0.30 - elapsed)
            await websocket.send(json.dumps({"method": method, "params": params, "id": request_id}))
            request_id += 1
            last_control_message_at = time.monotonic()

        while self._running:
            await self._subscription_changed.wait()
            self._subscription_changed.clear()
            # Scanner startup can register many symbols in the same event-loop
            # turn. Debounce them into one subscription command.
            await asyncio.sleep(0.30)
            self._subscription_changed.clear()
            desired = self._desired_streams()
            removed = sorted(current - desired)
            added = sorted(desired - current)
            if removed:
                await send_control("UNSUBSCRIBE", removed)
                current.difference_update(removed)
            if added:
                await send_control("SUBSCRIBE", added)
                current.update(added)
            self.subscribed_streams = len(current)

    async def _recover_gap(self, symbol: str, start: int, end: int) -> bool:
        missing = end - start + 1
        if missing <= 0:
            return True
        if missing > self.max_recovery_trades:
            return False
        next_id = start
        timeout = httpx.Timeout(4.0, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            while next_id <= end:
                limit = min(1000, end - next_id + 1)
                response = await client.get(
                    f"{self.rest_url}/api/v3/aggTrades",
                    params={"symbol": normalize_market_symbol(symbol), "fromId": next_id, "limit": limit},
                )
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list) or not rows:
                    return False
                progressed = False
                for row in rows:
                    sequence = int(row.get("a", -1))
                    if sequence < next_id:
                        continue
                    if sequence > end:
                        break
                    recovery_payload = {**row, "e": "aggTrade", "s": normalize_market_symbol(symbol)}
                    event = parse_binance_message(recovery_payload, recovered=True)
                    if event is None or event.sequence != sequence:
                        return False
                    await self._emit(event)
                    self.recovered_events += 1
                    next_id = sequence + 1
                    progressed = True
                if not progressed:
                    return False
        return next_id > end

    async def _process_event(self, event: NormalizedMarketEvent) -> None:
        if event.event_type == "trade" and event.sequence is not None:
            observation = self._sequence.observe(event.symbol, event.sequence)
            if observation.duplicate:
                self.duplicates_dropped += 1
                return
            if observation.gap_start is not None and observation.gap_end is not None:
                self.sequence_gap_count += 1
                recovered = False
                try:
                    recovered = await self._recover_gap(
                        event.symbol, observation.gap_start, observation.gap_end
                    )
                except Exception as exc:
                    self.last_error = f"gap_recovery:{type(exc).__name__}"
                    logger.warning(
                        "[Phase4] Binance trade-gap recovery failed for {} {}-{}: {}",
                        event.symbol, observation.gap_start, observation.gap_end, exc,
                    )
                if not recovered:
                    self.unrecoverable_gaps += 1
        await self._emit(event)

    async def _run(self) -> None:
        backoff = 1.0
        while self._running:
            current_streams: set[str] = set()
            subscription_task: Optional[asyncio.Task] = None
            try:
                async with websockets.connect(
                    self.ws_url,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=1_000_000,
                    max_queue=2048,
                ) as websocket:
                    self.connected = True
                    self.connected_at = time.time()
                    self.last_error = None
                    backoff = 1.0
                    self._subscription_changed.set()
                    subscription_task = asyncio.create_task(
                        self._sync_subscriptions(websocket, current_streams),
                        name="binance-subscriptions",
                    )
                    async for raw in websocket:
                        event = parse_binance_message(raw)
                        if event is None:
                            continue
                        self.events_received += 1
                        self.last_event_at = time.time()
                        if event.event_type == "trade":
                            latency = event.latency_ms
                            self.last_trade_latency_ms = latency
                            self.max_trade_latency_ms = max(self.max_trade_latency_ms, latency)
                            self.trade_latency_ema_ms = (
                                float(latency)
                                if self.trade_latency_ema_ms is None
                                else self.trade_latency_ema_ms * 0.95 + float(latency) * 0.05
                            )
                        await self._process_event(event)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_error = type(exc).__name__
                logger.warning("[Phase4] Binance WebSocket disconnected: {}", exc)
            finally:
                self.connected = False
                self.subscribed_streams = 0
                if subscription_task:
                    subscription_task.cancel()
                    await asyncio.gather(subscription_task, return_exceptions=True)
            if self._running:
                self.reconnect_count += 1
                await asyncio.sleep(backoff + random.uniform(0, backoff * 0.2))
                backoff = min(30.0, backoff * 2.0)

    def health_snapshot(self, *, now: Optional[float] = None, stale_after_seconds: float = 10.0) -> dict:
        current = now if now is not None else time.time()
        event_age = None if self.last_event_at is None else max(0.0, current - self.last_event_at)
        fresh = bool(self.connected and event_age is not None and event_age <= stale_after_seconds)
        return {
            "provider": "binance",
            "transport": "websocket",
            "running": self.is_running,
            "connected": self.connected,
            "fresh": fresh,
            "connected_at": self.connected_at,
            "last_event_at": self.last_event_at,
            "event_age_seconds": round(event_age, 3) if event_age is not None else None,
            "subscribed_streams": self.subscribed_streams,
            "events_received": self.events_received,
            "reconnect_count": self.reconnect_count,
            "duplicates_dropped": self.duplicates_dropped,
            "sequence_gap_count": self.sequence_gap_count,
            "recovered_events": self.recovered_events,
            "unrecoverable_gaps": self.unrecoverable_gaps,
            "cvd_integrity": "degraded" if self.unrecoverable_gaps else "complete",
            "last_trade_latency_ms": self.last_trade_latency_ms,
            "trade_latency_ema_ms": (
                round(self.trade_latency_ema_ms, 3)
                if self.trade_latency_ema_ms is not None else None
            ),
            "max_trade_latency_ms": self.max_trade_latency_ms,
            "skipped_symbols": list(self.skipped_symbols),
            "last_error": self.last_error,
        }
