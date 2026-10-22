# Intraday Multi-Timeframe Architecture (15M Execution Hierarchy)

> **Core Philosophy:** *Timeframe selection must strictly align with the planned holding period (30 minutes to 4 hours).*  
> Source Reference: Institutional MTF Best Practices & Fidelity Active Trader Guidelines.

---

## 1. Timeframe Roles & Responsibilities

| Timeframe | Institutional Role | Responsibility & Rule in Algorithm |
|---|---|---|
| **1D (Daily)** | **Macro Extremes** | Identifies Daily Key Zones (Daily Discount / Premium, Swing Extremes). Used to prevent buying at Daily All-Time Highs or selling at Daily Cycle Lows. |
| **4H** | **Macro Structure & Room to Target** | Identifies Macro Trend and **Major Obstacles / Boundaries**. Acts as the **Target Ceiling/Floor** rather than a blunt veto. Checks if there is adequate room ($R:R \ge 2.0$) before hitting 4H resistance/support. |
| **1H** | **Primary Intraday Trend** | **The true intraday compass.** 15M setups must align with 1H structure. If 1H shifts (CHoCH), intraday direction follows 1H. |
| **15M** | **Execution Authority** | **Sole execution engine.** Defines entry (OB/FVG), invalidation Stop Loss, causal event ID, and volume delta (CVD) confirmation. |
| **5M / 3M** | **Micro-Trigger & In-Flight Defense** | High-frequency protection. Detects micro-absorption and triggers **Dynamic Invalidation / Auto-Cancel** if an opposing impulse surge attacks the pending order. |

---

## 2. Decision Matrix (3 Core Scenarios)

### Scenario A: A+ Trend Continuation (Highest Probability)
- **4H:** Down $\to$ **1H:** Down $\to$ **15M:** Bounces up into Supply (Pullback).
- **Behavior:** The 15M upward bounce is a **Retracement into Premium**, NOT a buy signal.
- **Rule:** Wait for the 15M bounce to exhaust (Liquidity Sweep of highs + Bearish CVD confirmation) $\to$ **Authorize SHORT (Grade A)**.

### Scenario B: Intraday Counter-4H Swing (Reversal / Retracement Trade)
- **4H:** Down $\to$ **1H:** Shifts UP (1H CHoCH / Bullish Structure) $\to$ **15M:** UP with active demand setup.
- **Behavior:** 1H has decoupled from 4H to start an intraday multi-hour rally.
- **Rule:** **ALLOW LONG (Grade B)** provided that:
  1. **Room to Target Check:** Distance from Entry to the 4H Major Resistance must yield at least $R:R \ge 2.0$.
  2. **Target Cap:** Final TP is hard-capped before the lower boundary of the 4H Resistance Zone.
  3. If room to 4H resistance has $R:R < 2.0$, reject with `Insufficient room to 4H resistance: RR < 2.0 (No Chase)`.

### Scenario C: Macro Wall / Trap (No-Chase Protection)
- **Condition:** 15M, 1H, and 4H all appear Bullish, BUT price has arrived within $0.25 \times ATR$ of a Major 4H/1D Resistance Level.
- **Rule:** **REJECT / NO CHASE (`Adverse location: At major 4H/1D obstacle`)**. Never buy directly into a major macro supply zone.

---

## 3. Implementation Verification & Contract

- In `tri_core_engine.py`:
  - Replace absolute 4H counter-trend rejection with 1H Intraday alignment check + 4H Room-to-Target validation.
- In `execution_analysis.py`:
  - Calculate `room_to_target_rr` against 4H key levels when taking an intraday counter-4H setup.
- Log auditable reason codes: `ALIGNED_INTRADAY_CONTINUATION`, `COUNTER_4H_INTRADAY_SWING`, `NO_CHASE_4H_OBSTACLE`.
