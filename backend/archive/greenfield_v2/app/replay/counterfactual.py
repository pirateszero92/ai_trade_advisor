"""
Counterfactual Replay Engine (README.md Section 44)
Answers: For each opportunity, what happened if:
A) Market entered immediately?
B) Waited for structural limit at causal zone?
C) Skipped?

Provides the empirical statistical basis for calibrating execution policies.
"""

from dataclasses import dataclass
from typing import Any
import pandas as pd


@dataclass
class CounterfactualRecord:
    opportunity_id: str
    symbol: str
    direction: str
    strategy_family: str
    market_state: str
    created_at_bar: int

    # Branch A: Market Now
    market_eligible: bool
    market_entry: float
    market_rr: float
    market_filled: bool
    market_realized_r: float
    market_exit_reason: str
    market_holding_bars: int

    # Branch B: Wait for Limit
    limit_entry: float
    limit_rr: float
    limit_filled: bool
    limit_wait_bars: int
    limit_expired_without_fill: bool
    limit_invalidated_before_fill: bool
    limit_realized_r: float
    limit_exit_reason: str
    limit_holding_bars: int


@dataclass
class CounterfactualSummary:
    total_evaluated: int
    market_total_r: float
    market_expectancy_r: float
    market_win_rate: float
    
    limit_total_r: float
    limit_expectancy_r: float
    limit_win_rate: float
    limit_fill_rate: float
    limit_missed_rate: float
    
    expectancy_advantage_limit: float  # limit_expectancy_r - market_expectancy_r
    records: list[CounterfactualRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_evaluated": self.total_evaluated,
            "market_total_r": round(self.market_total_r, 4),
            "market_expectancy_r": round(self.market_expectancy_r, 4),
            "market_win_rate": round(self.market_win_rate, 4),
            "limit_total_r": round(self.limit_total_r, 4),
            "limit_expectancy_r": round(self.limit_expectancy_r, 4),
            "limit_win_rate": round(self.limit_win_rate, 4),
            "limit_fill_rate": round(self.limit_fill_rate, 4),
            "limit_missed_rate": round(self.limit_missed_rate, 4),
            "expectancy_advantage_limit": round(self.expectancy_advantage_limit, 4),
        }


class CounterfactualSimulator:
    """Evaluates counterfactual branches across historical forward bars."""

    @staticmethod
    def evaluate_opportunity(
        opp: dict[str, Any],
        start_bar_idx: int,
        df: pd.DataFrame,
        ttl_bars: int = 4,
        max_forward_bars: int = 50,
    ) -> CounterfactualRecord:
        """
        Simulates both Branch A (Market Now) and Branch B (Wait for Limit)
        against subsequent bars in df starting after start_bar_idx.
        """
        direction = str(opp.get("direction", "long")).lower()
        is_long = direction == "long"
        sl = float(opp.get("stop_loss", 0.0))
        tp = float(opp.get("take_profit", 0.0))
        structural_entry = float(opp.get("structural_entry", 0.0))
        structural_rr = float(opp.get("structural_rr", 0.0))
        
        # Trigger bar
        trigger_bar = df.iloc[start_bar_idx]
        market_entry = float(trigger_bar["close"])

        # Initial risk distance
        market_risk_dist = abs(market_entry - sl) if sl > 0 else 0.0
        market_reward_dist = abs(tp - market_entry) if tp > 0 else 0.0
        market_rr = (market_reward_dist / market_risk_dist) if market_risk_dist > 0 else 0.0

        # Future bars
        future_bars = df.iloc[start_bar_idx + 1 : start_bar_idx + 1 + max_forward_bars]

        # ----------------------------------------------------
        # Branch A: Market Now Simulation
        # ----------------------------------------------------
        m_realized_r = 0.0
        m_exit_reason = "TIMEOUT"
        m_holding = 0
        m_filled = market_risk_dist > 0

        if m_filled:
            for i, (_, bar) in enumerate(future_bars.iterrows(), start=1):
                high = float(bar["high"])
                low = float(bar["low"])
                
                # Check SL
                if (is_long and low <= sl) or (not is_long and high >= sl):
                    m_realized_r = -1.0
                    m_exit_reason = "STOP_LOSS"
                    m_holding = i
                    break
                # Check TP
                if (is_long and high >= tp) or (not is_long and low <= tp):
                    m_realized_r = market_rr
                    m_exit_reason = "TAKE_PROFIT"
                    m_holding = i
                    break

        # ----------------------------------------------------
        # Branch B: Wait for Limit Simulation
        # ----------------------------------------------------
        l_filled = False
        l_wait_bars = 0
        l_expired = False
        l_invalidated = False
        l_realized_r = 0.0
        l_exit_reason = "NO_FILL"
        l_holding = 0

        fill_bar_idx = -1
        for i, (_, bar) in enumerate(future_bars.iterrows(), start=1):
            if i > ttl_bars:
                l_expired = True
                l_exit_reason = "EXPIRED"
                break
                
            high = float(bar["high"])
            low = float(bar["low"])

            # Check if invalidated by SL before fill
            if (is_long and low <= sl) or (not is_long and high >= sl):
                l_invalidated = True
                l_exit_reason = "INVALIDATED_BEFORE_FILL"
                break

            # Check if limit touched
            if (is_long and low <= structural_entry) or (not is_long and high >= structural_entry):
                l_filled = True
                l_wait_bars = i
                fill_bar_idx = i
                break

        # If limit was filled, simulate forward trade from fill point
        if l_filled and fill_bar_idx > 0:
            post_fill_bars = future_bars.iloc[fill_bar_idx:]
            l_exit_reason = "TIMEOUT"
            for j, (_, bar) in enumerate(post_fill_bars.iterrows(), start=1):
                high = float(bar["high"])
                low = float(bar["low"])

                # Check SL
                if (is_long and low <= sl) or (not is_long and high >= sl):
                    l_realized_r = -1.0
                    l_exit_reason = "STOP_LOSS"
                    l_holding = j
                    break
                # Check TP
                if (is_long and high >= tp) or (not is_long and low <= tp):
                    l_realized_r = structural_rr
                    l_exit_reason = "TAKE_PROFIT"
                    l_holding = j
                    break

        return CounterfactualRecord(
            opportunity_id=str(opp.get("id", "")),
            symbol=str(opp.get("symbol", "")),
            direction=direction,
            strategy_family=str(opp.get("strategy_family", "")),
            market_state=str(opp.get("market_state", "")),
            created_at_bar=start_bar_idx,
            # Branch A
            market_eligible=market_rr >= 1.5,
            market_entry=market_entry,
            market_rr=market_rr,
            market_filled=m_filled,
            market_realized_r=m_realized_r,
            market_exit_reason=m_exit_reason,
            market_holding_bars=m_holding,
            # Branch B
            limit_entry=structural_entry,
            limit_rr=structural_rr,
            limit_filled=l_filled,
            limit_wait_bars=l_wait_bars,
            limit_expired_without_fill=l_expired,
            limit_invalidated_before_fill=l_invalidated,
            limit_realized_r=l_realized_r,
            limit_exit_reason=l_exit_reason,
            limit_holding_bars=l_holding,
        )

    @classmethod
    def summarize(cls, records: list[CounterfactualRecord]) -> CounterfactualSummary:
        n = len(records)
        if n == 0:
            return CounterfactualSummary(
                total_evaluated=0,
                market_total_r=0.0,
                market_expectancy_r=0.0,
                market_win_rate=0.0,
                limit_total_r=0.0,
                limit_expectancy_r=0.0,
                limit_win_rate=0.0,
                limit_fill_rate=0.0,
                limit_missed_rate=0.0,
                expectancy_advantage_limit=0.0,
                records=[],
            )

        # Market metrics
        m_rs = [r.market_realized_r for r in records if r.market_filled]
        m_total_r = sum(m_rs)
        m_exp_r = m_total_r / len(m_rs) if m_rs else 0.0
        m_wins = len([r for r in m_rs if r > 0.05])
        m_win_rate = m_wins / len(m_rs) if m_rs else 0.0

        # Limit metrics
        filled_records = [r for r in records if r.limit_filled]
        l_rs = [r.limit_realized_r for r in filled_records]
        l_total_r = sum(l_rs)
        l_exp_r = l_total_r / len(filled_records) if filled_records else 0.0
        l_wins = len([r for r in l_rs if r > 0.05])
        l_win_rate = l_wins / len(filled_records) if filled_records else 0.0

        limit_fill_rate = len(filled_records) / n
        missed = len([r for r in records if r.limit_expired_without_fill or r.limit_invalidated_before_fill])
        limit_missed_rate = missed / n

        return CounterfactualSummary(
            total_evaluated=n,
            market_total_r=m_total_r,
            market_expectancy_r=m_exp_r,
            market_win_rate=m_win_rate,
            limit_total_r=l_total_r,
            limit_expectancy_r=l_exp_r,
            limit_win_rate=l_win_rate,
            limit_fill_rate=limit_fill_rate,
            limit_missed_rate=limit_missed_rate,
            expectancy_advantage_limit=l_exp_r - m_exp_r,
            records=records,
        )
