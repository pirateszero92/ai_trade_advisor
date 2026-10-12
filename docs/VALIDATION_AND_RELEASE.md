# Validation & Release

## Validation hierarchy

A strategy change is not promoted merely because unit tests pass.

The intended progression is:

```text
Regression tests
→ deterministic replay
→ execution-aware backtest
→ anchored / rolling out-of-sample validation
→ paper trading evaluation
→ release gate
→ explicit operational approval
```

## Current policy status

The current TriCore policy remains marked:

```text
draft_unvalidated
```

until the configured walk-forward and release requirements are met.

## Regression requirements

Safety regression coverage protects:
- TriCore non-actionable → no AI request;
- AI CONFLICT/UNAVAILABLE → blocked when required;
- stale AI result → discarded;
- stale quote → rejected;
- wide spread → rejected;
- no-chase violation → rejected;
- low executable R:R → rejected;
- replaced event → rejected;
- invalid geometry → rejected;
- RiskEngine rejection → no OMS;
- OMS idempotency/restart/concurrency behavior.

## Walk-forward requirements

Walk-forward validation should:
- use chronological partitions;
- avoid future leakage;
- preserve execution assumptions;
- cover multiple symbols;
- cover different market regimes;
- report out-of-sample performance separately;
- avoid tuning on the final evaluation window.

## Paper trading evaluation

Track at minimum:
- candidates detected;
- TriCore-ready candidates;
- AI COHERENT / CONFLICT / UNAVAILABLE;
- execution validated/rejected;
- risk approved/rejected;
- submitted orders;
- fills/partial fills;
- closed outcomes.

Metrics include:
- win rate;
- profit factor;
- expectancy;
- R distribution;
- maximum drawdown;
- MFE/MAE;
- holding time;
- spread;
- slippage;
- fees;
- rejection reasons.

## Release gate

A production-eligible release must be tied to:
- a specific strategy/config version;
- test evidence;
- out-of-sample result;
- sufficient sample counts;
- explicit human approval where required.

Changing thresholds after validation invalidates the prior release evidence unless the change is explicitly non-behavioral.

## AI evaluation

AI must not be used to hide deterministic strategy weakness.

Compare at least:
- all deterministic candidates;
- AI COHERENT cohort;
- AI CONFLICT cohort;
- AI UNAVAILABLE cohort.

AI confidence never changes RiskEngine sizing.

## Release invariant

```text
No behavioral configuration is production-promoted without traceable out-of-sample evidence tied to the exact config version.
```
