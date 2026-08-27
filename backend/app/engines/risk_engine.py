"""
Risk Engine
Evaluates trade risk before execution: position sizing, R:R validation,
daily loss limits, drawdown management, and portfolio correlation warnings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Optional

from loguru import logger

from app.core.config import get_settings
from app.engines.smc_engine import SMCSignal


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RiskAssessment:
    """Result of a risk evaluation for a potential trade."""

    approved: bool = False
    rejection_reason: Optional[str] = None

    # Position sizing
    position_size: float = 0.0          # in units or contracts
    risk_amount: float = 0.0            # account currency at risk
    risk_pct: float = 0.0               # % of account at risk
    base_risk_pct: float = 0.0
    regime_risk_multiplier: float = 1.0
    market_regime: str = "legacy"

    # Levels
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: float = 0.0

    # Guardrails
    daily_loss_ok: bool = True
    position_count_ok: bool = True
    sl_valid: bool = True
    rr_ok: bool = True

    # Adjustment flags
    tone: Literal["normal", "cautious", "aggressive"] = "normal"
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Risk Engine
# ---------------------------------------------------------------------------

class RiskEngine:
    """
    Evaluates the risk profile of a proposed trade.

    Reads configuration from :class:`~app.core.config.Settings` and accepts
    optional portfolio state (open positions, daily P&L) to make holistic
    risk decisions.
    """

    MIN_RR = 1.5        # Minimum acceptable risk-reward ratio
    SL_MAX_PCT = 0.05   # Maximum SL distance as % of entry (5 %)

    def __init__(self):
        self.cfg = get_settings()

    def evaluate(
        self,
        signal: SMCSignal,
        account_balance: float = 10_000.0,
        open_positions: int = 0,
        daily_pnl_pct: float = 0.0,
        drawdown_pct: float = 0.0,
        contract_multiplier: float = 1.0,
        quantity_step: float = 0.000001,
        max_leverage: float = 5.0,
    ) -> RiskAssessment:
        """
        Evaluate the risk of trading a given SMCSignal.

        Parameters
        ----------
        signal:
            Populated SMCSignal from the SMC engine.
        account_balance:
            Current account balance in base currency.
        open_positions:
            Number of currently open positions.
        daily_pnl_pct:
            Today's realised P&L as a % of the account (negative = loss).
        drawdown_pct:
            Current open drawdown as a % of peak equity.

        Returns
        -------
        RiskAssessment
            Detailed risk assessment with approval decision.
        """
        assessment = RiskAssessment(
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_reward=signal.risk_reward,
        )

        numeric_inputs = (account_balance, open_positions, daily_pnl_pct, drawdown_pct,
                          contract_multiplier, quantity_step, max_leverage)
        if any(not math.isfinite(float(value)) for value in numeric_inputs):
            assessment.rejection_reason = "Risk inputs must be finite numbers"
            return assessment
        if account_balance <= 0:
            assessment.rejection_reason = "Account balance must be positive"
            return assessment
        if open_positions < 0 or contract_multiplier <= 0 or quantity_step <= 0 or max_leverage <= 0:
            assessment.rejection_reason = "Position count and instrument metadata are invalid"
            return assessment

        regime_data = getattr(signal, "market_regime", {})
        regime_policy = (
            regime_data.get("policy", {}) if isinstance(regime_data, dict) else {}
        )
        if regime_policy:
            assessment.market_regime = str(regime_data.get("regime", "unknown"))
            assessment.regime_risk_multiplier = float(
                regime_policy.get("risk_multiplier", 0.0)
            )
            if (
                not regime_data.get("ready", False)
                or not regime_policy.get("entry_allowed", False)
                or assessment.regime_risk_multiplier <= 0
            ):
                assessment.rejection_reason = (
                    f"New risk is blocked in {assessment.market_regime} regime"
                )
                return assessment

        # --- 1. Daily loss limit ---
        if daily_pnl_pct <= -self.cfg.max_daily_loss:
            assessment.daily_loss_ok = False
            assessment.approved = False
            assessment.rejection_reason = (
                f"Daily loss limit reached ({daily_pnl_pct:.2f}% vs limit -{self.cfg.max_daily_loss}%)"
            )
            return assessment

        # --- 2. Max open positions ---
        if open_positions >= self.cfg.max_open_positions:
            assessment.position_count_ok = False
            assessment.approved = False
            assessment.rejection_reason = (
                f"Max open positions reached ({open_positions}/{self.cfg.max_open_positions})"
            )
            return assessment

        # --- 3. Entry / SL validity ---
        if signal.entry is None or signal.stop_loss is None or signal.take_profit is None:
            assessment.sl_valid = False
            assessment.approved = False
            assessment.rejection_reason = "Missing entry, SL, or TP levels"
            return assessment

        levels = (signal.entry, signal.stop_loss, signal.take_profit)
        if any(not math.isfinite(float(level)) or float(level) <= 0 for level in levels):
            assessment.sl_valid = False
            assessment.rejection_reason = "Entry, SL, and TP must be positive finite numbers"
            return assessment

        if signal.direction == "long":
            geometry_valid = signal.stop_loss < signal.entry < signal.take_profit
        elif signal.direction == "short":
            geometry_valid = signal.take_profit < signal.entry < signal.stop_loss
        else:
            geometry_valid = False
        if not geometry_valid:
            assessment.sl_valid = False
            assessment.rejection_reason = "Entry, SL, and TP geometry does not match trade direction"
            return assessment

        sl_dist = abs(signal.entry - signal.stop_loss)
        if sl_dist == 0:
            assessment.sl_valid = False
            assessment.approved = False
            assessment.rejection_reason = "SL distance is zero"
            return assessment

        sl_pct = sl_dist / signal.entry
        if sl_pct > self.SL_MAX_PCT:
            assessment.sl_valid = False
            assessment.rejection_reason = (
                f"SL is too wide ({sl_pct*100:.2f}% of entry; max {self.SL_MAX_PCT*100:.0f}%)"
            )
            return assessment

        # --- 4. R:R check ---
        calculated_rr = abs(signal.take_profit - signal.entry) / sl_dist
        assessment.risk_reward = round(calculated_rr, 4)
        effective_min_rr = max(
            self.MIN_RR,
            float(regime_policy.get("min_rr", self.MIN_RR)) if regime_policy else self.MIN_RR,
        )
        if calculated_rr < effective_min_rr:
            assessment.rr_ok = False
            assessment.approved = False
            assessment.rejection_reason = (
                f"R:R too low ({calculated_rr:.2f} < {effective_min_rr})"
            )
            return assessment

        # --- 5. Position sizing ---
        base_risk_pct = self._adjust_risk(drawdown_pct, assessment)
        assessment.base_risk_pct = base_risk_pct
        risk_pct = base_risk_pct * assessment.regime_risk_multiplier
        if assessment.regime_risk_multiplier < 1.0:
            assessment.tone = "cautious"
            assessment.warnings.append(
                f"{assessment.market_regime.title()} regime — risk reduced to "
                f"{assessment.regime_risk_multiplier:.0%} of the drawdown-adjusted budget"
            )
        risk_budget = account_balance * (risk_pct / 100)
        
        # Instrument-aware sizing. contract_multiplier converts a one-point
        # move in one quantity unit into account currency.
        max_notional = account_balance * max_leverage
        max_units = max_notional / (signal.entry * contract_multiplier)
        raw_size = risk_budget / (sl_dist * contract_multiplier)
        capped_size = min(raw_size, max_units)
        position_size = math.floor(capped_size / quantity_step) * quantity_step
        if position_size <= 0:
            assessment.rejection_reason = "Account is too small for the instrument quantity step"
            return assessment

        actual_risk = position_size * sl_dist * contract_multiplier

        assessment.risk_pct = round(actual_risk / account_balance * 100.0, 4)
        assessment.risk_amount = round(actual_risk, 2)
        assessment.position_size = round(position_size, 6)

        # --- 6. Portfolio correlation warning ---
        if open_positions >= max(1, self.cfg.max_open_positions // 2):
            assessment.warnings.append(
                f"Portfolio concentration: {open_positions} positions open — watch correlation"
            )

        assessment.approved = True
        logger.info(
            f"[Risk] APPROVED {signal.symbol} | size={assessment.position_size} "
            f"risk={assessment.risk_pct:.2f}% rr={calculated_rr:.2f}"
        )
        return assessment

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _adjust_risk(self, drawdown_pct: float, assessment: RiskAssessment) -> float:
        """
        Dynamically adjust risk percentage based on current drawdown.

        Reduces risk when in drawdown, and scales up slightly in strong
        equity-curve conditions (no drawdown).

        Returns
        -------
        float
            Adjusted risk percentage per trade.
        """
        base = self.cfg.default_risk_per_trade

        if drawdown_pct >= 10:
            assessment.tone = "cautious"
            assessment.warnings.append(
                f"Drawdown {drawdown_pct:.1f}% — reducing risk to 0.5%"
            )
            return min(base * 0.5, 0.5)
        elif drawdown_pct >= 5:
            assessment.tone = "cautious"
            assessment.warnings.append(
                f"Drawdown {drawdown_pct:.1f}% — reducing risk to 0.75%"
            )
            return min(base * 0.75, 0.75)
        elif drawdown_pct > 0:
            assessment.tone = "cautious"
            return round(base * 0.9, 2)
        else:
            assessment.tone = "normal"
            return base
