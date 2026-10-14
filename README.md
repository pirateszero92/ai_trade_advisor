# AI Trade Advisor & Autonomous Execution Platform

> **Institutional-Grade Rule-Based & Advisory AI Trading System**  
> Operating on Deterministic Core Authorities, Canonical 15M Snapshots, Multi-bar Orderflow (SMC + CVD), Multimodal AI Blind Reviews, and Real-Time Risk Guardrails.

---

## 🏛️ System Architecture Overview

The platform is architected around strict **Authority Boundaries**:

```mermaid
flowchart TD
    MD["Market Data (15M Closed Bars + Flow)"] --> CS["Canonical 15M Snapshot"]
    CS --> TC["Tri-Core Engine (SMC + Multi-bar CVD)"]
    TC --> AI["AI Advisory Gate (Multimodal Review / Structured Advice)"]
    TC & AI --> ER["Execution Revalidator (No-Chase, R:R Floor, ATR Extension)"]
    ER --> RE["Risk Engine (Dynamic Position Sizing & Exposure)"]
    RE --> OMS["Transactional Paper / Live OMS (PostgreSQL Ledger)"]
    OMS & CS --> CR["Counterfactual Market Replay Engine (True AI Alpha Replay)"]
```

### Core Authority Pipeline Sequence:
1. **Market Data & Canonical Snapshot (`15m`)**: Continuous feed processing with anti-repaint index confirmation. 4H/1H serve as context; execution binds strictly to the closed 15M bar.
2. **Tri-Core Engine**: Deterministic detection of SMC structures (BOS, CHoCH, OB, FVG, Liquidity Sweeps) + Multi-bar CVD absorption/divergence confirmation.
3. **Independent Multimodal AI Review**: Blind chart & context analysis providing second-opinion confirmation (`COHERENT`, `CAUTION`, `CONFLICT`, `UNAVAILABLE`). AI acts strictly as an advisory veto/safety gate and **cannot** alter entry, stop loss, take profit, or position size.
4. **Execution Revalidator**: Dynamic order routing (Market vs. Causal Limit Retest based on 0.25 ATR extension), ensuring strict R:R execution floors (unified minimum R:R: 2.0) and quote freshness.
5. **Risk Engine**: Capital preservation guardrails enforcing dynamic position sizing, cluster correlation cooldowns, daily drawdown circuit breakers, and gross portfolio leverage limits.
6. **OMS (Order Management System)**: Transactional execution ledger with durable persistence, partial fills, auto-breakeven shields (+1.0R), and multi-tier trailing stops.
7. **Bar-by-bar Counterfactual Market Replay Engine**: Continuous deterministic simulation of candidate setups rejected or filtered by the AI gate against subsequent market OHLCV bars to rigorously quantify True AI Alpha, Avoided Losses, and Missed Profits without look-ahead bias.

---

## 🔬 Core Engineering Milestones (Phase 1 – Phase 5)

### Phase 1 & 2: Authority Pipeline & Status Transparency (`/api/v1/authority/status`)
* **Unified Minimum R:R (2.0)**: Synchronized across Execution Revalidator, Risk Engine, and strategy configurations, preventing sub-optimal setups from executing.
* **Stage Breakdown Readiness**: Comprehensive readiness inspection across Tri-Core, Systemic Risk, AI Gate, Execution, Risk, and OMS.
* **Full Decision Traceability**: Every proposed setup is indexed with immutable `decision_id`, `snapshot_id`, and `candidate_id` across all pipeline stages.

### Phase 3: Structured AI Decision & Clamping Guardrails
* **Structured Decision Schema**: AI responses are parsed and validated against strict schemas (`verdict`: `COHERENT`, `CAUTION`, `CONFLICT`, `UNAVAILABLE`, `PASS`).
* **Deterministic Risk Clamping**:
  * `COHERENT`: Risk score clamped to $\le 40$, multiplier $\le 1.0\text{x}$.
  * `CAUTION`: Multiplier clamped to $\le 0.5\text{x}$.
  * `CONFLICT` / `UNAVAILABLE`: Normalized to multiplier $0.0\text{x}$ and routed to `STAND_ASIDE`.
* **Causal Timing Advice**: Enforces timing directives (`IMMEDIATE`, `WAIT_CONFIRMATION`, `LIMIT_RETEST_ONLY`, `STAND_ASIDE`) without allowing AI to alter price geometry.

### Phase 4: Paper Observability, Funnel Analytics, & Cohorts (`/api/v1/paper/evaluation`)
* **Fill Rate Metric Granularity**: Distinguishes full fills (`orders_filled_full`) from partial fills (`orders_partially_filled`), exposing `order_fill_rate`, `partial_fill_rate`, and `execution_fill_rate`.
* **Cohort-Matched Funnel Conversion**: 1-to-1 candidate tracking ensures mathematically consistent denominators (e.g. `execution_validated_after_ai_coherent / ai_coherent_candidates`).
* **Final Fill State Hierarchy**: Prioritizes `filled > partial > open`, correctly classifying orders that progress from partial fills to complete fills.
* **Multi-Dimensional Cohorts**: Performance grouped by AI verdict, risk multiplier, market regime, AI timing advice, and realized entry timing.

### Phase 5: Bar-by-bar Counterfactual Market Replay Engine (`/api/v1/paper/counterfactual/replay`)
* **True AI Alpha Quantification**: Replays filtered candidates bar-by-bar to measure:
  * **Avoided Losses**: Losing trades correctly filtered out by AI (capital protected).
  * **Missed Profits**: Winning trades overly conservatively rejected by AI (opportunity cost).
  * **Net AI Filter Alpha**: $\text{Avoided Losses (\$) } - \text{Missed Profits (\$)}$.
* **Pure Tri-Core Baseline**: Synthesizes executed trades (unscaled) + replayed filtered trades to establish the true counterfactual benchmark of the deterministic engine without AI intervention.
* **Strict Next-Bar Execution Boundary (`strict_next_bar_open`)**: Replay strictly commences on the first open candle *after* candidate creation (`b_idx > occurred_ts`), eliminating intrabar look-ahead bias.
* **Assumption Fingerprinting & Auto-Invalidation**: Replay cache entries store SHA-256 hashes of fee, spread, slippage, and engine assumptions (`REPLAY_ENGINE_VERSION = "phase5.v2"`), auto-invalidating upon parameter changes.
* **Coverage Validation Threshold**: Requires $\ge 95\%$ replay coverage across eligible filtered candidates before elevating status from `replay_partial` to `counterfactual_replay_validated`.
* **Decisive Sample Size Gating**: Evaluates statistical reliability against closed decisive trades ($\ge 30$), flagging smaller cohorts as `insufficient_decisive_samples`.

---

## 🚀 Key Features

* **Deterministic Authority**: All trade geometry, risk budget, and order lifecycle transitions are governed by deterministic Python algorithms.
* **Causal Orderflow Confirmation**: Single-bar delta spikes are disregarded; requires continuous multi-bar aggressor delta, absorption, or CVD divergence.
* **Range Boundary Protection**: Strict boundary entry validation for Ping-Pong setups; entries inside the Equilibrium (mid-range) zone are blocked unconditionally.
* **Dynamic Position Sizing**: Automatic position size reduction for structural ATR stops up to the 5.0% absolute circuit breaker ceiling:
  $$\text{Position Size} = \frac{\text{Account Balance} \times \text{Risk Per Trade \%}}{\text{Structural Stop Distance} + \text{Cost}}$$
* **Two-Way UI/Backend Sync**: Mobile and Web dashboards reflect real-time backend runtime configurations (`GET /api/v1/settings/runtime`), eliminating UI desynchronization.
* **Interactive SMC Charting**: Custom high-performance canvas rendering Order Blocks, Fair Value Gaps, Swing Breaks, Live Ticker Badges, and Volume MA 20.
* **Fail-Safe Live Gate**: Live execution requires cryptographic time-limited sessions (InnovestX/MT5) and production-certified walk-forward criteria. Auto-Pilot defaults to Paper simulation for capital preservation.

---

## 📁 Repository Structure

```text
ai_trade_advisor/
├── backend/
│   ├── app/
│   │   ├── api/             # FastAPI REST & WebSocket endpoints (signals, chart, settings, paper)
│   │   ├── core/            # Configuration contracts, settings, and runtime stores
│   │   ├── engines/         # Tri-Core, SMC, Risk, Strategy, Indicators, and Backtest engines
│   │   ├── models/          # SQLAlchemy async database models
│   │   └── services/        # EventTrigger, ExecutionRevalidator, CandidateLifecycle, DecisionTrace
│   ├── config/              # Strategy definitions (strategy.yaml) and runtime configs
│   ├── tests/               # Pytest test suite (100+ comprehensive integration & unit tests)
│   └── Dockerfile           # Backend containerization
├── mobile/                  # Flutter Client (Web, Android APK, iOS)
│   ├── lib/
│   │   ├── core/            # Theme, API client, trading panels, and state providers
│   │   └── features/        # Chart, Signals, Settings, Journal, and Apex AI screens
│   └── pubspec.yaml
├── docs/                    # Formal technical specifications & architecture blueprints
├── docker-compose.yml       # Orchestration for FastAPI, PostgreSQL, Redis, and Web
└── README.md
```

---

## 🛠️ Quick Start & Deployment

### 1. Requirements
- Docker & Docker Compose
- Python 3.12+ (for local development)
- Flutter 3.24+ (for mobile/web compilation)

### 2. Environment Setup
```bash
cp backend/.env.example backend/.env
# Configure your Binance, InnovestX, and LLM API keys in backend/.env
```

### 3. Launching with Docker
```bash
docker-compose up -d --build
```
- **Web App**: `http://localhost:3000`
- **Backend API & Swagger Docs**: `http://localhost:8000/docs`
- **PostgreSQL Database**: Port `5432`
- **Redis Cache**: Port `6379`

### 4. Running Backend Tests
```bash
docker exec ai_trade_backend pytest tests/
```

### 5. Compiling Flutter Artifacts
```bash
# Build Flutter Web Release
cd mobile
flutter build web --release

# Build Android Release APK
flutter build apk --release
# Artifact location: mobile/build/app/outputs/flutter-apk/app-release.apk
```

---

## 🛡️ Operational Safeguards

| Safety Layer | Deterministic Rule |
| :--- | :--- |
| **Daily Loss Limit** | Hard halt when cumulative intraday PnL drops $\le -3.0\%$ (configurable). |
| **Max Open Positions** | Rejects new executions when active trades reach configured threshold. |
| **Cluster Cooldown** | Pauses subsequent trades in the same direction and correlated asset group following a stop loss. |
| **No-Chase Policy** | If market price extends $> 0.25$ ATR beyond structural entry, orders automatically pivot to Causal Limit Retests. |
| **AI Veto Gate** | AI `CAUTION` or `CONFLICT` verdicts prevent order dispatch, setting status to `Stand Aside`. |
| **BTC Systemic Risk Anchor** | Halts Altcoin trade executions during BTC extreme volatility or directional divergence. |

---

## 📜 License & Compliance
This software is built for automated trade decision assistance and paper execution modeling. Real-money live executions must always be validated against regulatory rules and verified broker API connectors.
