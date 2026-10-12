# Architecture & Authority

## Purpose

This document defines the current canonical architecture and decision ownership for AI Trade Advisor.

The authoritative pipeline is:

```text
Market Data
→ Canonical Snapshot
→ SMC + True CVD + Market Facts
→ TriCoreSetupEngine
→ AI Reviewer
→ ExecutionRevalidator
→ RiskEngine
→ OMS
```

## Core principle

Each component owns one boundary. No component may silently recreate an upstream decision.

### Market Data / Snapshot

Owns:
- source data identity;
- closed-candle boundary;
- timestamps and freshness;
- canonical snapshot identity.

Does not own:
- trade direction;
- setup eligibility;
- risk size;
- order execution.

### SMC Engine / CVD / Market Facts

Owns deterministic evidence:
- market structure;
- BOS / CHoCH;
- liquidity sweeps;
- order blocks;
- FVGs;
- breaker blocks;
- premium/discount location;
- exchange-derived delta/CVD;
- absorption/divergence evidence.

These facts are evidence. They do not submit orders.

### TriCoreSetupEngine

Owns:
- whether a setup exists;
- direction;
- setup type;
- structural Entry / SL / TP;
- structural R:R;
- entry policy;
- event identity;
- setup grade;
- expiration/invalidation;
- no-chase/extension policy;
- causal setup lineage.

TriCore is the deterministic setup authority.

### StrategyEngine

StrategyEngine is a thin projection/validation layer.

It may verify:
- actionable TriCore metadata;
- direction consistency;
- required structural levels;
- geometry;
- schema consistency.

It must not recreate:
- setup discovery;
- direction;
- SMC/CVD gating;
- R:R ownership;
- portfolio sizing.

### AI Reviewer

AI receives a completed deterministic candidate and supplied context.

Allowed output:
- COHERENT;
- CONFLICT;
- UNAVAILABLE;
- short reason/conflict evidence;
- optional management context.

AI cannot:
- create a new trade;
- reverse direction;
- edit Entry/SL/TP;
- change risk;
- size a position;
- authorize execution independently.

When AI is required, only a valid current COHERENT review can continue. Failure is not approval.

### ExecutionRevalidator

Owns "can the original setup still be entered now?"

Checks include:
- snapshot/event identity;
- snapshot expiry;
- fresh quote and quote age;
- spread;
- no-chase/ATR extension;
- rounded geometry;
- causal limit-zone validity;
- executable R:R;
- modeled fees/slippage assumptions.

It may reject a valid structural setup because current execution conditions changed.

It may not create a replacement setup.

### RiskEngine

Owns capital allocation and final risk approval:
- equity;
- daily loss;
- drawdown;
- max positions;
- exposure/concentration;
- correlated cluster controls;
- executable R:R floor;
- execution cost;
- stop distance;
- leverage/quantity filters;
- risk amount;
- risk percentage;
- position size.

No LLM confidence signal may modify risk sizing.

### OMS

OMS receives an already approved order specification.

Owns:
- idempotent placement;
- market/limit order state;
- fills/partial fills;
- cancellation;
- protection;
- breakeven;
- trailing;
- closing;
- recovery after restart.

OMS may enforce broker/instrument constraints but may not reinterpret strategy.

## Structural versus executable R:R

Structural R:R:
- computed from TriCore structural plan;
- belongs to deterministic setup logic.

Executable R:R:
- computed later using executable entry and execution costs;
- belongs to execution/risk validation.

They are deliberately separate.

## Context classification

Canonical context metadata classifies data as:
- execution_authority;
- deterministic_evidence;
- context_only;
- research_only.

Higher-timeframe 1D/4H/1H context is research-only/context-only and has no execution authority.

## Candidate lifecycle

```text
DETECTED
→ TRI_CORE_READY
→ AI_REVIEW_PENDING
→ AI_COHERENT | AI_CONFLICT | AI_UNAVAILABLE
→ EXECUTION_VALIDATING
→ EXECUTION_VALIDATED | EXECUTION_REJECTED
→ RISK_APPROVED | RISK_REJECTED
→ ORDER_PENDING
→ OPEN | PARTIALLY_FILLED
→ CLOSED | INVALIDATED | EXPIRED
```

Rules:
- invalid backward transitions are rejected;
- terminal candidates cannot reopen;
- replaced events invalidate old candidates;
- stale asynchronous AI results cannot mutate replacement candidates.

## UI authority model

UI must display the authority chain as separate states:
- TriCore;
- AI Review;
- Execution;
- Risk;
- OMS.

The UI must not merge these into one approval score.

## Canonical invariant

```text
TriCore defines the trade.
AI reviews the trade.
ExecutionRevalidator checks current enterability.
RiskEngine defines capital and size.
OMS executes and manages the order.
```
