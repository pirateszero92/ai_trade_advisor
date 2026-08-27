# Phase 6: Production-Grade Paper OMS

Phase 6 replaces JSON-owned Paper execution state with a transactional,
restart-safe PostgreSQL Order Management System. It does not place Live broker
orders, add an indicator, or weaken the Phase 0 Paper/Live boundary.

Status: implemented, migrated, deployed and restart-validated on 2026-08-27.

## Authority boundary

- PostgreSQL is authoritative for Paper accounts, positions, orders, fills and
  state transitions after application startup.
- `paper_trades_store.json` is now a compatibility projection for older
  readers. It is imported only when the active OMS account has no positions.
- A failed OMS startup makes Paper mutation/read APIs return `503`; production
  never silently falls back to JSON.
- Live broker state and `live_trades_store.json` are outside this service. The
  Paper OMS has no broker client and cannot create a Live Session.
- The Phase 3 normalized ledger continues to receive asynchronous Paper
  snapshots for analysis/replay, but it does not decide execution state.

## PostgreSQL model

| Table | Role |
| --- | --- |
| `paper_oms_accounts` | Versioned Paper account generations; reset retires an account instead of deleting audit history |
| `paper_oms_positions` | Authoritative aggregate quantity, average prices, protection, PnL and state |
| `paper_oms_orders` | Entry/reduce order state machine with unique account-scoped client order IDs |
| `paper_oms_fills` | Append-only executions with fee, spread, slippage, source sequence and latency |
| `paper_oms_events` | Append-only order/position transition audit |

Monetary and quantity fields use `NUMERIC(28,10)`. Fills and events have
PostgreSQL triggers that reject updates and deletes. One partial unique index
allows only one active Paper account generation.

## State machine

```text
Entry order
  submitted ──fill──> partially_filled ──fill──> filled
      │                    │
      └────cancel──────────┴──────────────> cancelled

Position
  pending ──first entry fill──> open ──reduce fills──> closed
      └────────cancel before fill───────────────────> cancelled
```

The order row and position row are locked in a fixed order inside one database
transaction. A cancel and fill cannot both win: whichever transaction locks
and commits first determines the valid next state; the loser re-reads the row
and returns an idempotent result or an explicit `409` conflict.

## Long/Short parity

Position effect is independent from order side:

| Direction | Entry | Exit / reduce | Geometry |
| --- | --- | --- | --- |
| Long | `buy` + `open` | `sell` + `reduce` | `SL < entry < TP` |
| Short, including Forex | `sell` + `open` | `buy` + `reduce` | `TP < entry < SL` |

PnL, protective exits, bid/ask selection and adverse slippage are
direction-aware. A Short exit is buy-to-cover; it is never interpreted as
selling a Long.

## Execution model

Every fill persists:

- midpoint/market reference and executable bid or ask;
- adverse slippage in basis points;
- fee based on executed notional;
- spread and slippage costs separately for analytics;
- fill quantity, liquidity label, data source, source sequence and latency.

The fill price already contains spread and slippage. Net realized PnL subtracts
fees once and does not double-count modeled spread/slippage costs.

Limit fills are liquidity-aware. Binance trade/book quantity is capped by
`PAPER_OMS_MAX_VOLUME_PARTICIPATION`; when a provider exposes no usable size,
the remaining order is filled by a configured fallback fraction per distinct
market event. Duplicate source/order execution keys are rejected.

Market orders use the freshest non-stale Price Hub quote and fill immediately.
If no fresh quote exists, the requested/manual reference is converted into a
clearly modelled synthetic bid/ask instead of being presented as exchange
execution.

## Partial fills and partial TP

- The first partial entry fill opens only the executed quantity while the
  remainder stays on the entry order.
- Cancelling a partially filled entry cancels only its unfilled remainder and
  preserves the open executed quantity.
- `quantity` or `percentage` on close creates an explicit reduce-only Paper
  order. Omitting both closes the full remaining quantity.
- A unique `idempotency_key` makes partial-close retries return the existing
  result instead of reducing twice.
- Closing while an entry remainder is active first cancels that remainder in
  the same transaction.

## Event-driven Auto-BE and trailing protection

Dynamic protection is owned by the PostgreSQL OMS, not the 1.5-second scanner
loop. It advances inside the same row-locked market-tick transaction that
checks TP/SL, preventing a fast +1R touch and reversal from being missed.

- At +1.0R, Auto-BE advances the stop beyond nominal entry to cover modeled
  accumulated entry fee, exit fee and adverse exit slippage.
- At +1.5R, trailing locks +0.6R.
- At +2.0R, trailing locks +1.2R.
- From +2.5R onward, the stop follows the favorable extreme at a distance of
  0.8R and only writes after a configurable minimum 0.05R improvement.
- Long uses executable bid and moves SL upward; Short uses executable ask and
  moves SL downward. A stop is never loosened.
- Favorable extreme, maximum R, stage and update time persist in PostgreSQL and
  recover after a backend restart. Every advance creates an append-only OMS
  event and a WebSocket `trade_updated` projection.

“Breakeven” means breakeven under the configured Paper fee/slippage model. A
real market gap or a future broker fill worse than the model can still lose
money and must never be presented as an absolute guarantee.

## Restart and reset recovery

Startup loads pending/open rows from PostgreSQL, rebuilds the active-symbol
set, registers those symbols with Price Hub and rewrites the JSON compatibility
projection. No in-memory order state is required for recovery.

Paper account reset with `clear_trades=true` retires the active generation and
creates a new one. Old orders, fills and audit events remain queryable in the
database and are never deleted by reset.

## API

- `POST /api/v1/paper/orders` — submit an idempotent Paper market/limit order.
- `GET /api/v1/paper/orders` — list authoritative positions with execution
  quantities and modeled costs.
- `GET /api/v1/paper/orders/{id}` — read one authoritative position.
- `GET /api/v1/paper/orders/{id}/fills` — inspect immutable fill details.
- `POST /api/v1/paper/orders/{id}/close` — cancel pending, close all, or reduce
  by `quantity`/`percentage`.
- `DELETE /api/v1/paper/orders/{id}` — cancel the active entry remainder.
- `GET /api/v1/paper/account` — account/equity calculated from OMS state.
- `GET /api/v1/paper/oms/status` — readiness and recovery report.
- `/ready` includes Paper OMS readiness and becomes degraded if it is down.

Legacy `/api/v1/trades` Paper routes delegate to the same OMS after startup so
older clients do not create a second source of execution truth.

## Configuration

```dotenv
PAPER_OMS_FEE_BPS=10
PAPER_OMS_SPREAD_BPS=5
PAPER_OMS_SLIPPAGE_BPS=3
PAPER_OMS_MAX_VOLUME_PARTICIPATION=0.01
PAPER_OMS_FALLBACK_PARTIAL_FILL_RATIO=0.35
PAPER_OMS_AUTO_BE_TRIGGER_R=1.0
PAPER_OMS_TRAILING_MIN_STEP_R=0.05
```

These assumptions are Paper-only. They do not configure an exchange or broker.

## Validation — 2026-08-27

- Alembic revision `20260827_0004` applied to PostgreSQL; protection stage,
  favorable extreme, maximum R and update timestamp are persisted.
- Initial bootstrap imported 19 Paper positions, 29 orders and 21 fills inside
  an all-or-nothing transaction.
- Backend restart recovered one open position with `legacy_imported=0`.
- Counts remained exactly `1 account / 19 positions / 29 orders / 21 fills /
  19 events` across the restart, proving no duplicate import.
- `/ready` returned `200` with `paper_oms.ready=true`.
- PostgreSQL contained both append-only audit triggers.
- Backend regression result: 106 passed, 2 skipped.
- Dedicated coverage validates Long and Short round trips, Forex Short side
  parity, modeled costs, partial entry fill, cancel-after-partial-fill,
  idempotent partial TP, cancelled-order non-fill, account generations,
  restart recovery, +1R fee-aware Auto-BE and every trailing tier.

## Remaining boundary

This phase is production-grade for Paper state handling, not proof of strategy
profitability and not a Live OMS. Broker reconciliation, native protective
orders, broker-side reduce-only behavior and Live restart recovery remain
Phase 9 and stay blocked by the evidence/safety gates.
