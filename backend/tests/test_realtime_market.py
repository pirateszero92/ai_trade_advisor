"""Phase 4 normalized stream, freshness and true-CVD regressions."""

from __future__ import annotations

import asyncio
import json
import time

import pandas as pd
import pytest

import app.engines.realtime_market as realtime_market_module
from app.engines.indicators import AdvancedIndicatorsEngine
from app.engines.market_data import MarketDataEngine
from app.engines.price_hub import price_hub
from app.engines.realtime_market import (
    BinanceRealtimeClient,
    NormalizedMarketEvent,
    TradeSequenceTracker,
    parse_binance_message,
)
from app.services.evidence import deserialize_market_window, serialize_market_window


@pytest.fixture
def isolated_price_hub():
    original_prices = price_hub._prices
    original_order_flow = price_hub._order_flow
    original_closed_candles = price_hub._closed_candles
    original_live_candles = price_hub._live_candles
    original_subscribers = price_hub._subscribers
    price_hub._prices = {}
    price_hub._order_flow = {}
    price_hub._closed_candles = {}
    price_hub._live_candles = {}
    price_hub._subscribers = []
    yield price_hub
    price_hub._prices = original_prices
    price_hub._order_flow = original_order_flow
    price_hub._closed_candles = original_closed_candles
    price_hub._live_candles = original_live_candles
    price_hub._subscribers = original_subscribers


def test_binance_book_ticker_is_normalized_without_false_gap_semantics():
    event = parse_binance_message({
        "u": 400900217,
        "s": "BTCUSDT",
        "b": "99.0",
        "B": "2.5",
        "a": "101.0",
        "A": "3.5",
    }, received_timestamp_ms=1_700_000_000_000)

    assert event is not None
    assert event.event_type == "quote"
    assert event.symbol == "BTC/USDT"
    assert event.price == 100.0
    assert event.sequence == 400900217


def test_binance_aggregate_trade_maps_market_maker_flag_to_aggressor_side():
    buy = parse_binance_message({
        "e": "aggTrade", "E": 1000, "T": 999, "s": "ETHUSDT",
        "a": 10, "p": "2500", "q": "2", "m": False,
    }, received_timestamp_ms=1010)
    sell = parse_binance_message({
        "e": "aggTrade", "E": 1020, "T": 1019, "s": "ETHUSDT",
        "a": 11, "p": "2499", "q": "1", "m": True,
    }, received_timestamp_ms=1030)

    assert buy is not None and buy.aggressor_side == "buy"
    assert sell is not None and sell.aggressor_side == "sell"
    assert buy.latency_ms == 10


def test_binance_closed_kline_uses_exchange_taker_volume_for_delta():
    event = parse_binance_message({
        "e": "kline",
        "E": 2_000,
        "s": "BNBUSDT",
        "k": {
            "t": 1_000, "T": 1_999, "i": "1m", "x": True,
            "o": "100", "h": "105", "l": "99", "c": "104",
            "v": "10", "V": "7",
        },
    }, received_timestamp_ms=2_010)

    assert event is not None and event.event_type == "candle"
    assert event.is_closed is True
    assert event.buy_volume == 7
    assert event.sell_volume == 3
    assert event.volume_delta == 4


def test_binance_24h_ticker_preserves_quote_and_session_statistics():
    event = parse_binance_message({
        "e": "24hrTicker", "E": 2_000, "s": "BTCUSDT",
        "c": "100", "b": "99", "B": "2", "a": "101", "A": "3",
        "P": "2.5", "h": "110", "l": "90", "v": "1000",
    }, received_timestamp_ms=2_010)

    assert event is not None
    assert event.bid == 99 and event.ask == 101
    assert event.high_24h == 110 and event.low_24h == 90
    assert event.change_24h == 2.5


def test_trade_sequence_tracker_detects_gaps_and_drops_duplicates():
    tracker = TradeSequenceTracker()
    assert tracker.observe("BTC/USDT", 100).accepted is True
    duplicate = tracker.observe("BTCUSDT", 100)
    gap = tracker.observe("BTCUSDT", 103)

    assert duplicate.accepted is False and duplicate.duplicate is True
    assert gap.accepted is True
    assert (gap.gap_start, gap.gap_end) == (101, 102)


@pytest.mark.anyio
async def test_stream_client_invokes_gap_recovery_before_current_trade(monkeypatch):
    emitted: list[int] = []
    recovered_ranges: list[tuple[int, int]] = []

    async def emit(event: NormalizedMarketEvent) -> None:
        emitted.append(int(event.sequence or 0))

    client = BinanceRealtimeClient(
        event_handler=emit,
        symbols_provider=lambda: {"BTC/USDT"},
    )

    async def recover(_symbol: str, start: int, end: int) -> bool:
        recovered_ranges.append((start, end))
        return True

    monkeypatch.setattr(client, "_recover_gap", recover)
    base = {
        "event_type": "trade",
        "symbol": "BTC/USDT",
        "source": "binance_ws",
        "exchange_timestamp_ms": 1000,
        "received_timestamp_ms": 1001,
        "price": 100.0,
        "quantity": 1.0,
        "aggressor_side": "buy",
    }
    await client._process_event(NormalizedMarketEvent(**base, sequence=10))
    await client._process_event(NormalizedMarketEvent(**base, sequence=13))
    await client._process_event(NormalizedMarketEvent(**base, sequence=13))

    assert recovered_ranges == [(11, 12)]
    assert emitted == [10, 13]
    assert client.sequence_gap_count == 1
    assert client.duplicates_dropped == 1


@pytest.mark.anyio
async def test_gap_recovery_replays_public_rest_aggregate_trades(monkeypatch):
    emitted: list[NormalizedMarketEvent] = []

    async def emit(event: NormalizedMarketEvent) -> None:
        emitted.append(event)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return [
                {"a": 11, "p": "100", "q": "2", "T": 1100, "m": False},
                {"a": 12, "p": "99", "q": "1", "T": 1200, "m": True},
            ]

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, url: str, *, params: dict):
            assert url.endswith("/api/v3/aggTrades")
            assert params == {"symbol": "BTCUSDT", "fromId": 11, "limit": 2}
            return FakeResponse()

    monkeypatch.setattr(realtime_market_module.httpx, "AsyncClient", FakeClient)
    client = BinanceRealtimeClient(
        event_handler=emit,
        symbols_provider=lambda: {"BTC/USDT"},
    )

    assert await client._recover_gap("BTC/USDT", 11, 12) is True
    assert [event.sequence for event in emitted] == [11, 12]
    assert [event.aggressor_side for event in emitted] == ["buy", "sell"]
    assert all(event.recovered for event in emitted)


@pytest.mark.anyio
async def test_watchlist_burst_is_coalesced_into_rate_limited_control_messages():
    symbols = {"BTC/USDT"}
    sent: list[tuple[float, dict]] = []

    class FakeWebSocket:
        async def send(self, raw: str) -> None:
            sent.append((time.monotonic(), json.loads(raw)))

    client = BinanceRealtimeClient(
        event_handler=lambda _event: None,
        symbols_provider=lambda: set(symbols),
    )
    client._running = True
    task = asyncio.create_task(client._sync_subscriptions(FakeWebSocket(), set()))
    try:
        for _ in range(10):
            client.refresh_subscriptions()
        await asyncio.sleep(0.4)
        assert len(sent) == 1
        assert sent[0][1]["method"] == "SUBSCRIBE"

        symbols.add("ETH/USDT")
        for _ in range(10):
            client.refresh_subscriptions()
        await asyncio.sleep(0.4)
        assert len(sent) == 2
        assert sent[1][0] - sent[0][0] >= 0.29
        assert all(stream.startswith("ethusdt@") for stream in sent[1][1]["params"])
    finally:
        client._running = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_price_hub_tracks_aggressor_cvd_closed_candles_and_staleness(isolated_price_hub):
    hub = isolated_price_hub
    now_ms = int(time.time() * 1000)
    for sequence, side, quantity in ((1, "buy", 3.0), (2, "sell", 1.0)):
        await hub.ingest_market_event(NormalizedMarketEvent(
            event_type="trade",
            symbol="BTC/USDT",
            source="binance_ws",
            exchange_timestamp_ms=now_ms - 5,
            received_timestamp_ms=now_ms,
            sequence=sequence,
            price=100.0,
            quantity=quantity,
            aggressor_side=side,
        ))

    flow = hub.get_order_flow("BTCUSDT")
    assert flow["buy_volume"] == 3
    assert flow["sell_volume"] == 1
    assert flow["volume_delta"] == 2
    assert flow["delta_ratio"] == 0.5
    assert flow["cvd"] == 2

    await hub.ingest_market_event(NormalizedMarketEvent(
        event_type="candle",
        symbol="BTC/USDT",
        source="binance_ws",
        exchange_timestamp_ms=now_ms,
        received_timestamp_ms=now_ms,
        price=104,
        timeframe="1m",
        candle_open_time_ms=now_ms - 60_000,
        candle_close_time_ms=now_ms - 1,
        open=100,
        high=105,
        low=99,
        close=104,
        volume=10,
        buy_volume=7,
        sell_volume=3,
        volume_delta=4,
        is_closed=True,
    ))
    candles = hub.get_closed_candles("BTCUSDT", "1m")
    assert len(candles) == 1
    assert candles[0]["flow_source"] == "binance_taker_volume"
    assert candles[0]["volume_delta"] == 4

    stale_ms = now_ms - 120_000
    hub.update_price(
        "ETH/USDT", 2500,
        source="binance_ws",
        exchange_timestamp_ms=stale_ms,
        received_timestamp_ms=stale_ms,
    )
    assert hub.get_ticker("ETHUSDT")["is_stale"] is True
    assert hub.get_price("ETHUSDT", allow_stale=False) is None


@pytest.mark.anyio
async def test_market_data_consumers_use_fresh_price_hub_before_rest(isolated_price_hub):
    hub = isolated_price_hub
    now_ms = int(time.time() * 1000)
    hub.update_price(
        "BTC/USDT", 101.0,
        bid=100.5,
        ask=101.5,
        change_24h=2.0,
        high_24h=110.0,
        low_24h=90.0,
        volume_24h=500.0,
        source="binance_ws",
        exchange_timestamp_ms=now_ms - 3,
        received_timestamp_ms=now_ms,
    )

    ticker = await MarketDataEngine().get_ticker_24h("BTC/USDT", "crypto")
    assert ticker["price"] == 101.0
    assert ticker["best_bid"] == 100.5
    assert ticker["best_ask"] == 101.5
    assert ticker["exchange"] == "binance_ws"
    assert ticker["data_quality"] == "true_realtime"


def test_volume_delta_prefers_exchange_aggressor_volume_over_candle_shape():
    rows = 12
    frame = pd.DataFrame({
        "open": [100.0] * rows,
        "high": [101.0] * rows,
        "low": [98.0] * rows,
        "close": [99.0] * rows,
        "volume": [100.0] * rows,
        "buy_volume": [80.0] * rows,
        "sell_volume": [20.0] * rows,
    })

    result = AdvancedIndicatorsEngine.compute_volume_delta(frame)
    assert result.delta == 60
    assert result.delta_ratio == 0.6
    assert result.source == "exchange_aggressor"


def test_evidence_round_trip_preserves_aggressor_volume_for_replay():
    frame = pd.DataFrame({
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "volume": [10.0, 20.0],
        "buy_volume": [7.0, 8.0],
        "sell_volume": [3.0, 12.0],
        "volume_delta": [4.0, -4.0],
        "cvd": [4.0, 0.0],
        "flow_source": ["binance_taker_volume", "binance_taker_volume"],
    }, index=pd.date_range("2026-01-01", periods=2, freq="min", tz="UTC"))

    restored = deserialize_market_window(serialize_market_window(frame))
    assert restored["buy_volume"].tolist() == [7.0, 8.0]
    assert restored["sell_volume"].tolist() == [3.0, 12.0]
    assert restored["flow_source"].tolist() == [
        "binance_taker_volume", "binance_taker_volume",
    ]
