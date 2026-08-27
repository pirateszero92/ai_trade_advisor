# Phase 5: Ordered Multi-Timeframe Decision Hierarchy

Phase 5 makes `4H Market Bias -> 1H SMC Setup -> 15m Entry Trigger` one
deterministic entry authority shared by Chart, Proactive Scanner, manual
analysis, evidence replay and execution-aware backtesting.

It does not add an indicator. Every role reuses only:

1. SMC Structure;
2. Volume Delta/CVD;
3. Squeeze Momentum.

## Why the stages are ordered gates

Scores from separate timeframes are not averaged. A strong 15m score cannot
overrule an opposite 4H structure, and a 4H bias cannot create an executable
order before the 1H Setup and 15m Trigger are ready.

The state progression is:

- `BLOCKED`: a parent direction is neutral/opposite or required data is not
  ready;
- `WATCH`: parent direction is valid but the setup, trigger or final Strategy
  Gate still needs confirmation;
- `READY`: all three role gates and the existing Strategy/Regime policy pass.

Only `READY` is actionable. AI receives the deterministic result downstream
and cannot change it.

## Role profiles

Profiles live under `timeframe_profiles` in `backend/config/strategy.yaml`.
Each role owns:

- timeframe and closed-candle lookback;
- external/internal swing sensitivity;
- Order Block and FVG horizons;
- ATR length;
- role-specific weights/parameters for the same three indicators;
- minimum confluence, data readiness and structural requirements.

Default responsibilities:

| Role | TF | Responsibility |
|---|---:|---|
| Bias | 4H | Authorize Long or Short market direction |
| Setup | 1H | Require a non-opposing SMC structure and aligned Order Block |
| Trigger | 15m | Require a directional BOS, CHoCH, liquidity sweep or squeeze release |

The final 15m signal must still pass the universal Strategy Gate and adaptive
Market Regime policy. Profile thresholds are candidate defaults and are not a
profitability claim; Phase 3 OOS/Paper validation remains mandatory.

## Shared snapshot and evidence

All three frames use completed candles only. A composite `snapshot_id`
fingerprints the three OHLCV windows and exact configuration. Chart and Scanner
reuse the cached result until the next trigger candle can close.

New evidence events include all three market windows and the complete MTF
decision. Offline replay reconstructs the hierarchy without fetching live
data and includes the MTF result in the deterministic decision hash.

## Backtesting

When Phase 5 profiles are enabled, OOS backtests must run on the configured
trigger timeframe (`15m` by default). The engine resamples only completed
trigger candles into 1H and 4H role windows and reports
`strategy_pipeline=phase5_mtf_hierarchy`. A request for another base timeframe
fails closed instead of evaluating a strategy that differs from runtime.

## APIs and clients

- `GET /api/v1/signals/mtf-matrix` returns the canonical matrix;
- `GET /api/v1/settings/timeframe-profiles` reads active profiles;
- `PUT /api/v1/settings/timeframe-profiles` validates and atomically saves
  profile changes;
- Chart overlay and Scanner payloads embed the same `mtf` decision;
- Flutter Chart and Signals render the 4H/1H/15m role state.

## Remaining validation

- run equal-calendar-window OOS comparisons by asset/provider;
- accumulate sufficient completed Paper trades per regime;
- calibrate profile parameters without using the OOS set;
- add optional 1D macro context only if evidence shows incremental value;
- add regime hysteresis as a separate state-stability change.

Live promotion remains blocked by Phase 3 Release Gate, Paper validation and
human approval.
