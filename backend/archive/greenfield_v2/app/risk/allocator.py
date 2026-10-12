"""
Portfolio-Aware Risk Allocator (RISK-001 to RISK-004)
Evaluates trade risk, portfolio gross limits, asset clusters, and daily circuit breakers.
Supports both PRE_CHECK (arming limit) and FINAL_CHECK (submission/fill).
"""

from datetime import datetime, timezone
import math
from typing import Any

from app.domain.execution import ExecutionIntent
from app.domain.opportunity import Opportunity, TradeDirection
from app.domain.reason_codes import ReasonCode
from app.domain.risk import RiskAssessment, RiskStage
from app.services.instrument_rules import InstrumentRules


# Asset cluster mapping (Section 23, RISK-003)
ASSET_CLUSTERS: dict[str, str] = {
    "BTC/USDT": "crypto_l1",
    "ETH/USDT": "crypto_l1",
    "SOL/USDT": "crypto_l1",
    "BNB/USDT": "crypto_l1",
    "DOGE/USDT": "crypto_meme",
    "SHIB/USDT": "crypto_meme",
    "PEPE/USDT": "crypto_meme",
    "EUR/USD": "forex_majors",
    "GBP/USD": "forex_majors",
    "USD/JPY": "forex_majors",
    "XAU/USD": "precious_metals",
}


class RiskAllocator:
    """Authoritative allocator of portfolio risk and position size."""

    def __init__(
        self,
        base_risk_per_trade_pct: float = 0.50,
        max_risk_per_trade_pct: float = 1.00,
        max_daily_loss_pct: float = 3.00,
        max_open_positions: int = 5,
        max_cluster_positions: int = 2,
        max_portfolio_open_risk_pct: float = 5.00,
    ) -> None:
        self.base_risk_per_trade_pct = base_risk_per_trade_pct
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_open_positions = max_open_positions
        self.max_cluster_positions = max_cluster_positions
        self.max_portfolio_open_risk_pct = max_portfolio_open_risk_pct

    def allocate(
        self,
        opportunity: Opportunity,
        intent: ExecutionIntent,
        account_equity: float,
        daily_pnl_pct: float,
        active_positions: list[dict[str, Any]],
        stage: RiskStage = RiskStage.FINAL_CHECK,
        rules: InstrumentRules | None = None,
    ) -> RiskAssessment:
        rejections: list[str] = []
        warnings: list[str] = []

        # 1. Daily Loss Circuit Breaker (RISK-004)
        if daily_pnl_pct <= -self.max_daily_loss_pct:
            return RiskAssessment(
                opportunity_id=opportunity.opportunity_id,
                lineage_id=opportunity.lineage_id,
                stage=stage,
                approved=False,
                account_equity=account_equity,
                daily_pnl_pct=daily_pnl_pct,
                reason_code=ReasonCode.DAILY_LOSS_LIMIT,
                rejection_reasons=[
                    f"Daily loss limit breached ({daily_pnl_pct:.2f}% <= -{self.max_daily_loss_pct:.2f}%)"
                ],
            )

        # 2. Maximum Open Positions (RISK-002)
        open_pos_count = len(active_positions)
        if open_pos_count >= self.max_open_positions:
            return RiskAssessment(
                opportunity_id=opportunity.opportunity_id,
                lineage_id=opportunity.lineage_id,
                stage=stage,
                approved=False,
                account_equity=account_equity,
                daily_pnl_pct=daily_pnl_pct,
                open_positions_count=open_pos_count,
                reason_code=ReasonCode.MAX_POSITIONS_REACHED,
                rejection_reasons=[
                    f"Maximum open positions reached ({open_pos_count}/{self.max_open_positions})"
                ],
            )

        # 3. Asset Cluster Concentration (RISK-003)
        symbol = opportunity.symbol
        cluster = ASSET_CLUSTERS.get(symbol, "other")
        cluster_positions = [
            p for p in active_positions
            if ASSET_CLUSTERS.get(p.get("symbol", ""), "other") == cluster
            and p.get("direction") == opportunity.direction.value
        ]
        cluster_count = len(cluster_positions)

        if cluster != "other" and cluster_count >= self.max_cluster_positions:
            return RiskAssessment(
                opportunity_id=opportunity.opportunity_id,
                lineage_id=opportunity.lineage_id,
                stage=stage,
                approved=False,
                account_equity=account_equity,
                daily_pnl_pct=daily_pnl_pct,
                open_positions_count=open_pos_count,
                cluster_positions_count=cluster_count,
                reason_code=ReasonCode.CLUSTER_EXPOSURE_LIMIT,
                rejection_reasons=[
                    f"Cluster limit reached for '{cluster}' ({cluster_count}/{self.max_cluster_positions})"
                ],
            )

        # 4. Position Sizing (RISK-001)
        # Cluster scale down by 50% for 2nd position in same cluster
        cluster_multiplier = 0.5 if cluster_count == 1 else 1.0
        if cluster_multiplier < 1.0:
            warnings.append(f"Cluster '{cluster}' 2nd position: risk scaled down to 50%")

        allocated_risk_pct = min(self.base_risk_per_trade_pct * cluster_multiplier, self.max_risk_per_trade_pct)
        risk_budget_usd = account_equity * (allocated_risk_pct / 100.0)

        entry = intent.executable_entry
        stop = intent.stop_loss
        stop_dist = abs(entry - stop)

        if stop_dist <= 0:
            return RiskAssessment(
                opportunity_id=opportunity.opportunity_id,
                lineage_id=opportunity.lineage_id,
                stage=stage,
                approved=False,
                account_equity=account_equity,
                reason_code=ReasonCode.INVALID_POSITION_SIZE,
                rejection_reasons=["Stop loss distance is zero"],
            )

        raw_qty = risk_budget_usd / stop_dist

        # Apply instrument rules normalization
        if rules is not None:
            norm_qty = rules.floor_quantity(raw_qty)
            notional = norm_qty * entry
            if norm_qty < rules.min_quantity:
                return RiskAssessment(
                    opportunity_id=opportunity.opportunity_id,
                    lineage_id=opportunity.lineage_id,
                    stage=stage,
                    approved=False,
                    account_equity=account_equity,
                    reason_code=ReasonCode.MINIMUM_NOTIONAL_UNMET,
                    rejection_reasons=[
                        f"Calculated quantity {norm_qty} below instrument min_quantity {rules.min_quantity}"
                    ],
                )
            if rules.min_notional > 0 and notional < rules.min_notional:
                return RiskAssessment(
                    opportunity_id=opportunity.opportunity_id,
                    lineage_id=opportunity.lineage_id,
                    stage=stage,
                    approved=False,
                    account_equity=account_equity,
                    reason_code=ReasonCode.MINIMUM_NOTIONAL_UNMET,
                    rejection_reasons=[
                        f"Calculated notional ${notional:.2f} below instrument min_notional ${rules.min_notional:.2f}"
                    ],
                )
        else:
            norm_qty = round(raw_qty, 6)
            notional = norm_qty * entry

        return RiskAssessment(
            opportunity_id=opportunity.opportunity_id,
            lineage_id=opportunity.lineage_id,
            stage=stage,
            approved=True,
            allocated_risk_pct=allocated_risk_pct,
            risk_budget_usd=round(risk_budget_usd, 2),
            position_size=norm_qty,
            position_notional=round(notional, 2),
            account_equity=account_equity,
            daily_pnl_pct=daily_pnl_pct,
            open_positions_count=open_pos_count,
            cluster_positions_count=cluster_count,
            reason_code=ReasonCode.RISK_PRECHECK_OK if stage == RiskStage.PRE_CHECK else ReasonCode.RISK_APPROVED,
            warnings=warnings,
        )
