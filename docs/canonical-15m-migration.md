# Canonical 15M migration and validation contract

## Non-negotiable authority boundary

- Every executable decision uses one completed 15M candle snapshot.
- Chart overlays, Scanner, WebSocket payloads, Strategy, Risk, AI context and
  OMS metadata share its `snapshot_id`, `candle_digest`, `closed_at` and
  `valid_until`.
- 1H/4H/1D may be displayed or studied, but cannot score, approve, reject,
  redirect or resize an order.
- AI is advisory-only. `CONFLICT` is visible and auditable but cannot silently
  override the deterministic plan.

## Threshold contract

`tri_core_policy` in `backend/config/strategy.yaml` owns the migration values.
The initial TTL, ATR and minimum R:R values have
`calibration_status: draft_unvalidated`; they are hypotheses, not performance
claims. Every setup and Paper order records the policy version and status.

A policy cannot pass the release gate until its status is
`walk_forward_validated`. Changing a threshold requires a new policy version,
fresh replay evidence and human approval. Do not tune on the same OOS fold used
for acceptance.

## Phase 0 data-readiness gate

Calibration requires empirical 15M candles containing provider-derived taker
buy and taker sell volume. Candle anatomy estimates are presentation-only.
Rows must be finite, non-negative, identify a trusted provider and reconcile
`buy_volume + sell_volume` to total volume within 2%.

- Replay readiness: at least 95% valid aggressor rows.
- Promotion readiness: replay-ready data spanning at least 90 days, at least
  two regimes and multiple symbols.
- Synthetic BTC/ETH fixtures are regression tests only and are forbidden for
threshold calibration or expectancy claims.

Binance Klines expose total base volume and taker-buy base volume. The system
derives taker-sell volume and 15M delta from those aggregate fields. This is
`kline_derived` exchange aggressor CVD—not tick-level CVD. Live closed candles
built from Binance `aggTrades` are labelled `aggtrade_derived`. The two may be
compared in research, but their granularity must never be hidden or described
as identical.

The backtest API can page up to 40,000 Binance klines. At 15M, 10,000 bars are
about 104 days and one year is roughly 35,040 bars. If a venue cannot provide trustworthy historical aggressor
volume, collect live shadow data; do not substitute estimated CVD.

## Entry lifecycle

`ZONE_APPROACH -> ARMED -> SWEEP_DETECTED -> FLOW_CONFIRMED` then one of:

- `ENTRY_READY / MARKET_ELIGIBLE`
- `LIMIT_RETEST_ONLY` at a causal OB/FVG tied to the event
- `NO_CHASE`, `EXPIRED` or `INVALIDATED`

Transitions use a stable event ID and can emit pre-alerts. A pending causal
limit is cancelled if that same event expires or invalidates. Runtime UI entry
preferences cannot turn `LIMIT_RETEST_ONLY` into a market order.

## Validation design

Use chronological, anchored walk-forward folds:

1. Fit/calibrate only on the training window.
2. Freeze the policy version.
3. Evaluate the following non-overlapping OOS window with fees, spread,
   slippage, latency, partial fills and unfilled limits.
4. Roll forward and repeat without leaking future candles.
5. Aggregate OOS folds across symbols and regimes.

The MVP may begin with one chronological 70/30 split for rapid feedback. Full
certification requires at least 3 non-overlapping rolling/anchored OOS folds;
random shuffle is prohibited.

## Sprint 1 exit and rollback criteria

The 15M build remains Shadow/Paper during Sprint 1. Compare it with the frozen
1H baseline over the same symbols and wall-clock period. Hold promotion and
retain the 1H baseline if any of these occur:

- 15M OOS expectancy is not positive;
- maximum drawdown or false-entry rate is materially worse than baseline;
- data-readiness falls below 95% or snapshot/replay mismatch is non-zero;
- the improvement is concentrated in one symbol or one regime;
- alert latency improves only by accepting a statistically unacceptable rise
  in invalidated setups.

Rollback means disabling 15M Auto-Pilot and continuing Shadow/Paper collection;
it does not silently switch production authority back and forth. Thresholds
are recalibrated on the next training fold, assigned a new policy version and
tested again on a new unseen OOS window.

The release gate requires sample size, positive expectancy, profit factor,
drawdown, realistic fill rate, regime diversity, OOS mode, true-aggressor data
coverage, history duration and a validated policy. Passing still does not
enable Live or Auto-Pilot automatically.

## Implementation status (2026-09-09)

1. **MVP-1 — implemented:** canonical closed-15M execution snapshots,
   immutable identity metadata, paginated Binance Kline aggressor history,
   readiness/reconciliation checks and replay fixtures are in place.
2. **MVP-2 — implemented in Shadow/Paper:** stable causal event IDs, Hybrid TTL,
   the full lifecycle, causal OB/FVG limit lineage, MARKET/LIMIT/NO_CHASE entry
   policy, transition pre-alerts and invalidation cancellation are wired into
   Scanner and Auto-Pilot.
3. **MVP-3 — implemented for Paper OMS:** partial fills, fixed-risk sizing,
   1R breakeven, trailing protection, daily circuit breaker and recovery of
   active orders/positions after restart are persisted. Live remains disabled.
4. **Hardening — partially implemented:** lifecycle de-duplication uses Redis
   atomic compare-and-set with an atomic disk fallback; the default backtest API
   now runs at least three chronological, non-overlapping OOS folds; empirical
   reclaim/extension percentiles are exported as diagnostics only; SQZ has an
   explicit baseline ablation.

Still intentionally not promoted: automatic threshold selection and a validated
production policy. Percentiles must first be converted into a bounded candidate
grid on training/inner-validation data, frozen, then pass multi-symbol rolling
outer-OOS evaluation. Until that empirical run exists, the checked-in policy
remains `draft_unvalidated`, all activity remains Shadow/Paper, and no software
change may claim live expectancy.

## Original effort estimate

1. **MVP-1 — data integrity (1–2 days):** canonical 15M snapshot, immutable
   metadata, exchange-aggressor readiness report and baseline replay fixtures.
2. **MVP-2 — deterministic entry (2–4 days):** lifecycle, Hybrid TTL,
   MARKET/LIMIT/NO_CHASE policy and pre-alert transitions.
3. **MVP-3 — Paper execution (2–4 days):** partial-fill-aware limits,
   invalidation cancellation, fixed-risk sizing, breakeven/trailing and daily
   halt.
4. **Hardening (3–7 days plus data collection):** persistent alert state,
   parameter-grid calibration, multi-symbol walk-forward report and OI/SQZ
   ablations.

Calendar time depends on data availability. Engineering can ship in slices;
statistical promotion must wait for enough empirical OOS samples.
