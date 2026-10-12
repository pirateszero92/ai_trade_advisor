# Failure, Audit & Replay

## Failure semantics

### AI

Canonical AI outcomes:
- timeout → UNAVAILABLE;
- provider/network failure → UNAVAILABLE;
- malformed response → UNAVAILABLE;
- schema-invalid response → UNAVAILABLE;
- unsupported timeframe/context failure → UNAVAILABLE;
- material contradiction → CONFLICT;
- valid non-contradictory review → COHERENT.

A stale, expired, or replaced asynchronous result is discarded rather than applied.

When AI review is required, UNAVAILABLE and CONFLICT stop the order path.

### Execution

Execution rejection reasons include:
- stale quote;
- wide spread;
- snapshot expiry;
- replaced event;
- no-chase violation;
- excessive extension;
- invalid rounded geometry;
- invalid/mitigated limit zone;
- executable R:R below minimum.

Execution failure never falls back to the structural setup as if execution were still valid.

### Risk

Risk rejection stops OMS submission.

Examples:
- daily-loss limit;
- drawdown;
- max positions;
- concentration;
- insufficient executable R:R;
- excessive execution cost;
- invalid stop distance;
- invalid risk override;
- invalid quantity/leverage constraints.

### OMS

OMS must fail safely on:
- duplicate idempotency key;
- invalid order payload;
- broker/instrument constraint failures;
- stale or incomplete approved context.

OMS does not bypass earlier authority stages.

## Decision Trace

Decision Trace links one exact candidate identity across:
- snapshot;
- event;
- TriCore candidate;
- AI review;
- execution revalidation;
- RiskEngine;
- OMS state/fill/result.

Unknown facts stay absent. They are not fabricated.

Trace records include hashes so tampering can be detected.

## Immutable evidence

Immutable deterministic evidence contains the closed-candle/config inputs needed to reproduce a decision.

Decision Trace and immutable evidence serve different purposes:
- Evidence = deterministic replay input.
- Decision Trace = cross-layer execution timeline.

## Replay rules

Replay must:
- use closed candles only;
- freeze relevant config;
- prevent future leakage;
- preserve snapshot/event identity;
- separate deterministic output from AI review;
- use explicit execution assumptions.

Replay must not infer missing historical facts.

## Backtest rules

Backtest should evaluate:
- all deterministic candidates;
- COHERENT cohort;
- CONFLICT cohort;
- execution rejects;
- risk rejects;
- fills and costs.

AI must be evaluated as a separate cohort effect rather than blended into deterministic strategy logic.

## Audit identity

Forensic analysis should rely on:
- snapshot_id;
- event_id;
- decision_trace_id;
- policy/config hashes;
- candle digest;
- quote timestamp;
- order/fill identifiers.

Prometheus labels deliberately avoid high-cardinality candidate identifiers; Decision Trace provides candidate-level forensics.

## Replay invariant

```text
The same frozen evidence and config must reproduce the deterministic decision without using future data or inventing missing facts.
```
