# Greenfield Opportunity-Centric Architecture (V2 Prototype Archive)

## Overview & Archival Notice

This directory contains the experimental pure Domain-Driven Design (DDD) Greenfield Opportunity-Centric trading architecture prototype built during early sprint exploration.

### Why this is Archived:
In accordance with the **Architectural Directive: "Evolve, not Revolve"** (documented in project root [`README.md`](file:///c:/Users/arthit.n/git/ai_trade_advisor/README.md)), the project decided against running a risky greenfield rewrite in parallel with live trading. 

**80-85% of the battle-tested, hardened core** resides in the active production codebase:
- `TriCoreSetupEngine` (Setup Authority)
- `candidate_lifecycle.py` (State Machine with `WAITING_ENTRY` & `LIMIT_ARMED`)
- `execution_revalidator.py` (`ExecutionIntent`: `MARKET_NOW`, `PLACE_LIMIT`, `WAIT`, `CANCEL`)
- `decision_trace.py` (Append-only causal trace)
- `risk_engine.py` (Aggregate leverage & portfolio exposure checks)
- `persistent_risk_guard.py` (Postgres-backed circuit breaker)
- `paper_oms.py` (ACID transactional order execution)
- `AuthorityPipelineView` / `signals_screen.dart` (Mobile UI connected to `/api/v1/signals`)

To eliminate dual-stack maintenance overhead and prevent dead-code confusion, the greenfield modules and their standalone test suite have been safely archived here for future reference.

---

## Contents of this Archive

- `app/domain/`: Pure domain entities (`Opportunity`, `ExecutionDecision`, `Order`, `Position`, `ReasonCode`, `Clock`, `Ids`, `Trace`)
- `app/facts/`: Deterministic market facts engine (OB, FVG, Swings, Sweeps, CVD, ATR)
- `app/market_state/`: Market regime/environment classifier (Trend, Range, Compression)
- `app/setups/`: 4-family setup detectors (Reversal, Continuation, Range Fade, Breakout Retest)
- `app/opportunity/`: Opportunity Engine, state store, TTL invalidation
- `app/execution/`: Execution Planner with 3-tier R:R resolution
- `app/risk/`: Portfolio Risk Allocator prototype
- `app/oms/`: Greenfield memory-based OMS prototype
- `app/positions/`: Position manager with auto break-even
- `app/ai/`: Advisory-only AI reviewer prototype
- `app/replay/`: Bar-by-bar historical replay and counterfactual simulator
- `app/orchestrator.py`: Central DecisionOrchestrator wiring the 10 greenfield layers
- `app/api/opportunities_api.py`: FastAPI endpoints for `/api/v1/opportunities`
- `tests/`: 10 test suites (34 tests) covering the greenfield architecture components
