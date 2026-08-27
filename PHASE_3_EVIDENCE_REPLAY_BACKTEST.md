# Phase 3: Evidence, Replay & Backtesting

Phase 3 creates the proof layer required before strategy parameters can be
promoted toward Live trading. It does not add an indicator or change the
Paper/Live boundary.

## Milestone 3.1 — Immutable decision evidence

Status: implemented as the first Phase 3 vertical slice

Each full analysis and proactive scanner decision records an append-only event
containing:

- the canonical OHLCV window and its SHA-256 fingerprint;
- symbol, market, exchange, timeframe, HTF bias and entry mode;
- complete Strategy, Indicator Core and Regime Policy configuration snapshots;
- signal, strategy, risk and optional AI outputs;
- schema and engine versions plus content/config/decision fingerprints;
- event source and UTC timestamps.

The evidence API is read-only. PostgreSQL rejects updates and deletes on the
evidence table so a later result cannot silently rewrite the historical input.
Evidence persistence is advisory and cannot call a broker or grant a Live
Session.

## Milestone 3.2 — Deterministic replay

Status: implemented for single-event and persisted batch replay

- reconstruct the DataFrame from the stored market window;
- run `SMCEngine` and `StrategyEngine` with the stored configuration;
- compare replayed output fingerprints with the recorded decision;
- report mismatches explicitly rather than overwriting the original event;
- add batch replay by strategy version, symbol and market regime.

The batch API stores immutable reproducibility metrics including match rate,
integrity failures, deterministic mismatches and replay errors.

## Milestone 3.3 — Execution-aware backtesting

Status: initial deterministic OOS engine implemented

- simulate fee, bid/ask spread, slippage, funding and configurable latency;
- model limit misses, gaps, partial fills and cancel/fill races;
- use the same Strategy, Risk and Paper OMS rules as runtime;
- calculate expectancy in R, maximum drawdown, profit factor, MFE/MAE,
  fill quality and confidence calibration;
- segment results by asset, timeframe and market regime.

The simulator currently models configurable fee, half-spread, adverse
slippage, bar latency, limit-order timeout, volume participation, partial
entry fills and conservative same-bar SL/TP ordering. It reports expectancy in
R, profit factor, drawdown, fill rate, MFE/MAE, slippage, regime breakdown and
confidence calibration. Tick/order-book replay remains Phase 4 work.

### Long/Short parity invariant

Every market, including Forex, must support both directional exposures as
first-class trades. A `SELL` intent is not assumed to mean “close a Long”:
its position effect must explicitly distinguish opening/increasing a Short
from reducing/closing a Long.

- Long opens with a buy and closes with a sell; valid geometry is
  `stop_loss < entry < take_profit`.
- Short opens with a sell and closes with a buy-to-cover; valid geometry is
  `take_profit < entry < stop_loss`.
- PnL, fee, spread, adverse slippage, SL/TP collision ordering, MFE and MAE
  must remain direction-aware and have regression coverage for both sides.
- Future broker adapters must send an explicit position side and reduce-only
  intent where the venue supports them; ambiguous netting behavior must fail
  closed before Live submission.

## Milestone 3.4 — Release gates

Status: deterministic gate implemented; Paper validation remains required

- walk-forward and out-of-sample evaluation;
- minimum sample requirements per regime;
- versioned experiment and result records;
- candidate strategy must pass configured evidence thresholds before Paper;
- Paper performance and human approval are required before any Live canary.

Every gate result is immutable, always sets `human_approval_required=true`,
and keeps `production_eligible=false`. Passing numerical criteria never edits
strategy configuration or opens a Live Session.

## JSON ledger migration

On backend startup, the isolated Paper and Live compatibility ledgers are
mirrored idempotently into normalized PostgreSQL trade, order and fill records
for replay/analytics. Phase 6 has now completed the Paper execution cutover:
the dedicated PostgreSQL Paper OMS is authoritative and writes the Paper JSON
file as a compatibility projection. Live compatibility data remains isolated,
and the Phase 3 mirror still cannot submit an order or decide execution state.

## Phase acceptance criteria

- a recorded decision can be reproduced from its event without fetching live
  market data;
- every decision identifies its data/config/engine versions;
- missing or corrupted evidence fails replay with an explicit reason;
- repeated replay of the same event is deterministic;
- costs and execution assumptions are visible in every backtest report;
- Long and Short scenarios both pass execution and risk regression tests;
- no unvalidated strategy version is promoted automatically.
