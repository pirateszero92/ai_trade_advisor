# AI SMC+CVD Scanner rollout

## Current authority model

Every Chart, Scanner, WebSocket and backtest decision starts from the same closed
execution-timeframe snapshot. `TriCoreSetupEngine` is the sole entry authority:

1. SMC supplies causal location, invalidation and structural target.
2. Binance Kline taker volume supplies venue-specific `kline_derived` aggressor
   CVD intent; live aggTrades candles are labelled `aggtrade_derived`.
3. A setup must be either sweep + reclaim + CVD divergence/absorption, or
   displacement + fresh OB/FVG retest + aligned aggressor delta.
4. Structural R:R must be at least 2.0 before Strategy can approve it.

Scenario S1–S9, MTF/HTF, HMM, VPIN, derivatives sentiment and inducement remain
analytics or warnings. They cannot manufacture, veto, resize or redirect an order.

## AI advisory

The LLM is called only after a deterministic Tri-Core setup exists. It receives
structured closed OHLCV, causal SMC metadata, CVD evidence, optional SQZ context
and the already fixed deterministic plan. No other timeframe or news claim is
included.

The strict response is `COHERENT`, `CONFLICT` or `UNAVAILABLE`, with a reason,
conflicts, evidence and optional management note. It has no fields for direction,
entry, stop, target, size or order approval. A timeout, malformed response,
cross-timeframe reference or audit failure cannot change Strategy/Risk/OMS state.
The annotation retains the original snapshot ID.

`SCANNER_AI_MODE=shadow|paper|off` is retained for rollout compatibility, but both
`shadow` and `paper` are advisory-only. A non-setup is explicitly recorded as
`not_requested`; it is never presented as a completed model review.

## SQZ and risk

SQZ is not part of eligibility. No SQZ leaves the deterministic setup at 0.75%
base risk; maximum aligned bonus scales the request to 1.00%. Regime and cluster
multipliers may only reduce that budget. Paper OMS moves protection to breakeven
at 1R and persists a 24-hour circuit breaker after either 3% rolling loss or
three consecutive stop-loss exits.

## Evidence-based promotion

The legacy direction is saved in `migration_comparison` for observational shadow
metrics only. Backtest evaluates every closed bar (`stride_bars=1`), uses the same
Tri-Core/Strategy/Risk path, universal 1R breakeven and rolling halt. Ablation now
compares fixed-risk `SMC_CVD_BASE` with `SMC_CVD_SQZ_BONUS`; invalid SMC-only and
SMC+SQZ variants were removed because CVD is mandatory.

Auto-Pilot remains disabled by default. Enabling it still requires a fresh quote,
instrument rules, capacity/correlation controls and an approved RiskAssessment.
No backtest or AI result enables Auto-Pilot automatically.

The detailed migration, threshold-calibration and data-readiness contract is in
[`canonical-15m-migration.md`](canonical-15m-migration.md). In particular, the
current TTL/ATR/R:R values are draft hypotheses and cannot pass promotion until
at least three chronological, non-overlapping rolling OOS folds mark a new
policy version as validated. The backtest exports empirical timing/extension
distributions but deliberately does not auto-select thresholds from outer OOS.
