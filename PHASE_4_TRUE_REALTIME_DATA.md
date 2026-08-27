# Phase 4: True Real-Time Market Data

Phase 4 replaces upstream crypto quote polling with an event-driven public
market-data pipeline. It does not add an indicator and cannot submit Paper or
Live orders.

Status: implemented, deployed and runtime-validated for Binance Spot. A
broker-native Forex/Gold stream remains a separate provider integration; its
current Yahoo source is deliberately labelled as a fallback.

## Provider support matrix

| Market | Current source | Transport | Quality label |
| --- | --- | --- | --- |
| Binance Spot crypto | book ticker, 24h ticker, aggregate trades and klines | WebSocket | `true_realtime` |
| Binance during disconnect/staleness | public 24h ticker | REST every 5 seconds | `polling_fallback` |
| Forex and Gold | Yahoo quote fallback | REST every 15 seconds | `delayed_polling_fallback` |
| Equities | Yahoo quote fallback | REST every 15 seconds | `delayed_polling_fallback` |

Forex is decentralized, so Yahoo candle volume must never be represented as
exchange-wide aggressor volume or true CVD. A broker-native MT5 tick bridge is
still required before Forex/Gold can be labelled true real-time. Long and
Short analysis remain equally supported; market-data events themselves are
direction-neutral.

## Event pipeline

```text
Binance public WebSocket
  bookTicker + 24hrTicker + aggTrade + closed kline
                         |
                         v
              NormalizedMarketEvent
                         |
          sequence check / REST gap recovery
                         |
                         v
                    Price Hub
        quote + freshness + CVD + closed candles
             |                         |
             v                         v
   event-driven client WS       MarketDataEngine
     20ms burst coalescing     analysis/replay snapshot
```

The public stream follows the official
[Binance Spot WebSocket Streams](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)
aggregate-trade, book-ticker, 24-hour ticker and kline schemas. No exchange API
key or broker credential is sent to the market-data endpoint.

## Sequence and reconnect safety

- Aggregate-trade IDs are tracked independently per symbol.
- Duplicate and out-of-order aggregate trades are dropped.
- A missing aggregate-trade range is recovered from the public REST
  `aggTrades` endpoint before the current event is applied.
- Gaps larger than the configured recovery bound fail visibly as
  `cvd_integrity=degraded`; they are never silently labelled complete.
- WebSocket reconnect uses bounded exponential backoff and resubscribes the
  active symbol set.
- Best-book update IDs are retained for traceability but are not incorrectly
  treated as consecutive aggregate-trade IDs.

## Freshness and fallback

Every Price Hub entry records exchange time, receive time, latency, source,
transport and quality. WebSocket entries become stale after 10 seconds by
default; polling fallbacks use a separate 45-second threshold. A disconnected
or stale Binance stream activates REST fallback while the WebSocket reconnects.

Consumers read the shared Price Hub before making an outbound quote request.
This removes duplicate quote polling from chart, trade and monitor paths.
Only exchange-confirmed closed candles are overlaid onto analytical OHLCV, so
indicators cannot repaint from a currently forming candle.

## Canonical analysis snapshot

Chart Overlay and Proactive Scanner no longer choose independent windows. Both
read `AnalysisSnapshotService`, configured in `strategy.yaml` with 300 LTF and
120 HTF completed candles. The snapshot owns the HTF bias and exposes a stable
ID, last closed-candle timestamps and lookback metadata. The Chart API returns
the candles and overlay together from that same snapshot, so two HTTP requests
cannot cross a candle close and disagree with each other.

## True aggressor CVD

- Live CVD uses Binance aggregate trades. `m=true` means the buyer was maker,
  therefore the aggressor was the seller; `m=false` is an aggressive buy.
- Historical Binance candles use taker-buy base volume (`V`) and total base
  volume (`v`) to calculate buy volume, sell volume and volume delta.
- When a provider exposes only OHLCV, the previous candle-anatomy calculation
  remains available but is explicitly labelled
  `estimated_candle_anatomy`, never `exchange_aggressor`.
- Aggressor fields are preserved inside Phase 3 evidence snapshots so replay
  evaluates the same CVD input as the original decision.

## Observability API

- `GET /api/v1/market-data/status` reports connectivity, event age,
  reconnects, duplicate drops, sequence gaps, recovery counts, CVD integrity
  and per-symbol quality.
- `GET /api/v1/market-data/order-flow` reports live aggressor CVD and recent
  exchange-confirmed closed-candle delta.
- `/ready` includes market-data status and the number of fresh real-time
  symbols without turning an external exchange outage into a process crash.

## Configuration

```dotenv
MARKET_STREAM_ENABLED=true
BINANCE_MARKET_WS_URL=wss://stream.binance.com:9443/stream
BINANCE_MARKET_REST_URL=https://data-api.binance.vision
BINANCE_KLINE_INTERVALS=1m,15m,1h,4h,1d
MARKET_STREAM_MAX_SYMBOLS=100
MARKET_STREAM_MAX_RECOVERY_TRADES=5000
MARKET_DATA_STALE_AFTER_SECONDS=10
MARKET_DATA_FALLBACK_STALE_AFTER_SECONDS=45
```

## Acceptance criteria

- normalized parser tests cover quotes, both aggressor directions and closed
  candles;
- duplicate, sequence-gap, REST-recovery and stale-data behavior fail visibly;
- strategy CVD prefers exchange aggressor volume when present;
- fallback volume cannot be mislabelled as true CVD;
- evidence replay preserves aggressor inputs;
- the full backend test suite passes;
- deployment telemetry confirms `connected=true`, `fresh=true` and live event
  growth for Binance before the provider is considered operational.

## Deployment validation — 2026-08-26

- backend container rebuilt and healthy;
- 14 Binance symbols and 112 upstream streams subscribed;
- more than 42,000 normalized events processed during the validation window;
- no reconnects, sequence gaps or unrecoverable gaps after subscription
  control-message rate limiting was applied;
- CVD integrity reported `complete`;
- aggregate-trade latency EMA was approximately 43 ms during the sampled
  window;
- backend resource sample at live load was approximately 5.8% CPU and 157 MiB
  memory;
- backend regression result: 91 passed, 2 skipped.
