"""
Risk Engine
Evaluates trade risk before execution: position sizing, R:R validation,
daily loss limits, drawdown management, and portfolio correlation warnings.
"""

from __future__ import annotations

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

        sl_dist = abs(signal.entry - signal.stop_loss)
        if sl_dist == 0:
            assessment.sl_valid = False
            assessment.approved = False
            assessment.rejection_reason = "SL distance is zero"
            return assessment

        sl_pct = sl_dist / signal.entry
        if sl_pct > self.SL_MAX_PCT:
            assessment.sl_valid = False
            assessment.warnings.append(
                f"Wide SL: {sl_pct*100:.2f}% of entry (max {self.SL_MAX_PCT*100:.0f}%)"
            )

        # --- 4. R:R check ---
        if signal.risk_reward < self.MIN_RR:
            assessment.rr_ok = False
            assessment.approved = False
            assessment.rejection_reason = (
                f"R:R too low ({signal.risk_reward:.2f} < {self.MIN_RR})"
            )
            return assessment

        # --- 5. Position sizing ---
        risk_pct = self._adjust_risk(drawdown_pct, assessment)
        risk_amount = account_balance * (risk_pct / 100)
        position_size = risk_amount / sl_dist if sl_dist > 0 else 0.0

        assessment.risk_pct = risk_pct
        assessment.risk_amount = round(risk_amount, 2)
        assessment.position_size = round(position_size, 6)

        # --- 6. Portfolio correlation warning ---
        if open_positions >= self.cfg.max_open_positions // 2:
            assessment.warnings.append(
                f"Portfolio concentration: {open_positions} positions open — watch correlation"
            )

        assessment.approved = True
        logger.info(
            f"[Risk] APPROVED {signal.symbol} | size={assessment.position_size} "
            f"risk={assessment.risk_pct:.1f}% rr={signal.risk_reward:.2f}"
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
        elif drawdown_pct == 0:
            assessment.tone = "normal"
            return base

        return base
