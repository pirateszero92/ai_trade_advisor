"""
Risk Engine
Evaluates trade risk before execution: position sizing, R:R validation,
daily loss limits, drawdown management, and portfolio correlation/cluster controls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Literal, Optional

from loguru import logger

from app.core.config import get_settings
from app.engines.smc_engine import SMCSignal


# ---------------------------------------------------------------------------
# Asset Cluster Definitions for Cross-Asset Beta Risk Management
# ---------------------------------------------------------------------------

ASSET_CLUSTERS: dict[str, str] = {
    # Crypto Layer 1 / High-Beta
    "BTC/USDT": "crypto_l1",
    "BTCUSDT": "crypto_l1",
    "ETH/USDT": "crypto_l1",
    "ETHUSDT": "crypto_l1",
    "SOL/USDT": "crypto_l1",
    "SOLUSDT": "crypto_l1",
    "BNB/USDT": "crypto_l1",
    "BNBUSDT": "crypto_l1",
    "ADA/USDT": "crypto_l1",
    "ADAUSDT": "crypto_l1",
    "XRP/USDT": "crypto_l1",
    "XRPUSDT": "crypto_l1",
    "AVAX/USDT": "crypto_l1",
    "AVAXUSDT": "crypto_l1",
    "SUI/USDT": "crypto_l1",
    "SUIUSDT": "crypto_l1",

    # Crypto Memes / Micro-cap Alts
    "DOGE/USDT": "crypto_meme",
    "DOGEUSDT": "crypto_meme",
    "SHIB/USDT": "crypto_meme",
    "SHIBUSDT": "crypto_meme",
    "PEPE/USDT": "crypto_meme",
    "PEPEUSDT": "crypto_meme",
    "WIF/USDT": "crypto_meme",
    "WIFUSDT": "crypto_meme",

    # Precious Metals & Commodities
    "XAUUSD": "precious_metals",
    "XAU/USD": "precious_metals",
    "GOLD": "precious_metals",
    "XAGUSD": "precious_metals",

    # Forex Majors
    "EURUSD": "forex_majors",
    "GBPUSD": "forex_majors",
    "USDJPY": "forex_majors",
    "AUDUSD": "forex_majors",
    "USDCAD": "forex_majors",
    "USDCHF": "forex_majors",

    # US Equities
    "AAPL": "us_equities",
    "TSLA": "us_equities",
    "NVDA": "us_equities",
    "MSFT": "us_equities",
    "AMZN": "us_equities",
}


def get_asset_cluster(symbol: str) -> str:
    """Resolve asset cluster category for correlation-aware risk allocation."""
    clean = symbol.upper().replace("-", "").replace("_", "")
    if clean in ASSET_CLUSTERS:
        return ASSET_CLUSTERS[clean]
    for key, cluster in ASSET_CLUSTERS.items():
        if clean == key.upper().replace("/", ""):
            return cluster
    if ("USDT" in clean or "USD" in clean) and any(c in clean for c in ("BTC", "ETH", "SOL", "BNB", "ADA")):
        return "crypto_l1"
    if any(fx in clean for fx in ("EUR", "GBP", "JPY", "AUD", "CAD", "TRY", "CHF", "NZD")):
        return "forex_majors"
    return "other"


def validate_btc_anchor(
    symbol: str,
    direction: str,
    systemic_risk: dict[str, Any] | None = None,
) -> tuple[bool, Optional[str]]:
    """Validate BTC Market Anchor for crypto altcoins."""
    from app.services.btc_market_anchor import validate_btc_anchor as _val
    return _val(symbol, direction, systemic_risk)


def squeeze_adjusted_risk_pct(signal: SMCSignal) -> float:
    """Optionally translate SQZ strength into sizing; never create or veto a setup."""
    cfg = get_settings()
    base = min(float(cfg.tri_core_base_risk_pct), float(cfg.default_risk_per_trade), 1.0)
    if not bool(getattr(cfg, "sqz_risk_scaling_enabled", False)):
        return round(base, 4)

    cap = min(max(base, float(cfg.tri_core_max_risk_pct)), 1.0)
    decision = getattr(signal, "indicator_decision", {}) or {}
    bonus = float(decision.get("squeeze_bonus", 0) or 0)
    bonus_max = max(float(decision.get("squeeze_bonus_max", 10) or 10), 1.0)
    strength = min(max(bonus / bonus_max, 0.0), 1.0)
    return round(base + (cap - base) * strength, 4)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RiskAssessment:
    """Result of a risk evaluation for a potential trade."""

    approved: bool = False
    rejection_reason: Optional[str] = None

    # Position sizing
    sizing_authority: str = "risk_engine"
    risk_budget_source: str = "deterministic_config"
    position_size: float = 0.0          # in units or contracts
    risk_amount: float = 0.0            # account currency at risk
    risk_pct: float = 0.0               # % of account at risk
    stop_distance_pct: float = 0.0
    max_stop_distance_pct: float = 0.0
    atr_pct: float | None = None
    stop_distance_atr: float | None = None
    stop_policy: str = "absolute"
    base_risk_pct: float = 0.0
    regime_risk_multiplier: float = 1.0
    cluster_risk_multiplier: float = 1.0
    market_regime: str = "legacy"
    asset_cluster: str = "other"

    # Levels
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: float = 0.0
    structural_rr: float = 0.0
    executable_rr: float = 0.0
    minimum_executable_rr: float = 0.0
    execution_cost_per_unit: float = 0.0

    # Guardrails
    daily_loss_ok: bool = True
    position_count_ok: bool = True
    cluster_exposure_ok: bool = True
    portfolio_notional_ok: bool = True
    sl_valid: bool = True
    rr_ok: bool = True

    # Portfolio exposure
    current_gross_notional: float = 0.0
    new_position_notional: float = 0.0
    portfolio_gross_notional: float = 0.0
    max_portfolio_notional: float = 0.0

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
    portfolio state (open positions, active clusters, daily P&L) to make holistic
    risk decisions.
    """

    MIN_RR = 1.5              # Minimum acceptable risk-reward ratio
    SL_MAX_PCT = 0.05         # Maximum SL distance as % of entry (5 %)
    # Candidate seed only. Walk-forward calibration may replace this value;
    # the absolute 5% circuit breaker above remains authoritative.
    ATR_STOP_MULTIPLIER = 1.25
    SL_MIN_PCT = 0.0025       # Minimum SL distance as % of entry (0.25 %) to avoid fee explosion & 1-tick noise
    MAX_FEE_TO_RISK_RATIO = 0.50  # Max ratio of per-unit execution cost to SL distance (50%)
    MAX_CLUSTER_POSITIONS = 2 # Max concurrent positions in same correlated cluster

    # Drawdown risk throttling constants
    DD_10_MULTIPLIER = 0.50
    DD_10_MAX_RISK_CAP_PCT = 0.50
    DD_5_MULTIPLIER = 0.75
    DD_5_MAX_RISK_CAP_PCT = 0.75
    DD_LIGHT_MULTIPLIER = 0.90

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
        current_gross_notional: float = 0.0,
        active_positions: Optional[list[dict[str, Any]]] = None,
        execution_cost_per_unit: float = 0.0,
        risk_per_trade_pct: float | None = None,
        minimum_rr: float | None = None,
        max_stop_distance_pct: float | None = None,
        min_stop_distance_pct: float | None = None,
        atr_pct: float | None = None,
    ) -> RiskAssessment:
        """
        Evaluate the risk of trading a given SMCSignal.
        """
        cluster = get_asset_cluster(signal.symbol)
        structural_rr = float(getattr(signal, "risk_reward", 0.0) or 0.0)
        assessment = RiskAssessment(
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_reward=structural_rr,
            structural_rr=structural_rr,
            asset_cluster=cluster,
        )

        numeric_inputs = (account_balance, open_positions, daily_pnl_pct, drawdown_pct,
                          contract_multiplier, quantity_step, max_leverage,
                          current_gross_notional, execution_cost_per_unit)
        if any(not math.isfinite(float(value)) for value in numeric_inputs):
            assessment.rejection_reason = "Risk inputs must be finite numbers"
            return assessment
        if account_balance <= 0:
            assessment.rejection_reason = "Account balance must be positive"
            return assessment
        if (open_positions < 0 or contract_multiplier <= 0 or quantity_step <= 0
                or max_leverage <= 0 or current_gross_notional < 0
                or execution_cost_per_unit < 0):
            assessment.rejection_reason = "Position count and instrument metadata are invalid"
            return assessment

        # BTC Market Anchor systemic risk check
        systemic_risk = getattr(signal, "systemic_risk", {}) or {}
        if systemic_risk and systemic_risk.get("execution_allowed", True) is False:
            assessment.approved = False
            assessment.rejection_reason = (
                f"BTC Market Anchor: {systemic_risk.get('reason') or systemic_risk.get('reason_code') or 'BTC_REGIME_CONFLICT'}"
            )
            return assessment

        regime_data = getattr(signal, "market_regime", {})
        # Regime is sizing context only. RiskEngine must not become a competing
        # setup/entry authority by reading ready/entry_allowed gates.
        if isinstance(regime_data, dict) and regime_data:
            assessment.market_regime = str(regime_data.get("regime", "unknown"))
            regime_policy = regime_data.get("policy")
            if isinstance(regime_policy, dict) and regime_policy:
                multiplier = regime_policy.get("risk_multiplier", 1.0)
                try:
                    multiplier = float(multiplier)
                except (TypeError, ValueError):
                    multiplier = 1.0
                if not math.isfinite(multiplier) or multiplier <= 0:
                    multiplier = 1.0
                    assessment.warnings.append(
                        "Invalid regime risk multiplier ignored; using neutral sizing"
                    )
                assessment.regime_risk_multiplier = multiplier
            else:
                assessment.warnings.append(
                    "Regime policy unavailable; using neutral risk multiplier"
                )
        else:
            regime_policy = {}

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

        # --- 3. Correlation & Cluster Exposure Control ---
        cluster_multiplier = 1.0
        if active_positions and cluster != "other":
            same_cluster_pos = [
                p for p in active_positions
                if get_asset_cluster(p.get("symbol", "")) == cluster
                and p.get("status") in ("open", "pending")
                and (not signal.direction or p.get("direction") == signal.direction)
            ]
            if len(same_cluster_pos) >= self.MAX_CLUSTER_POSITIONS:
                assessment.cluster_exposure_ok = False
                assessment.approved = False
                assessment.rejection_reason = (
                    f"Cluster exposure limit reached (max {self.MAX_CLUSTER_POSITIONS} positions in '{cluster}' cluster)"
                )
                return assessment
            elif len(same_cluster_pos) == 1:
                # 2nd position in same cluster: scale risk down by 50%
                cluster_multiplier = 0.50
                assessment.warnings.append(
                    f"Cluster concentration: scaling 2nd position in '{cluster}' by 50%"
                )
        assessment.cluster_risk_multiplier = cluster_multiplier

        # --- 4. Entry / SL validity ---
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
        effective_max_stop_pct = self.SL_MAX_PCT
        if max_stop_distance_pct is not None:
            if not math.isfinite(float(max_stop_distance_pct)) or not 0 < float(max_stop_distance_pct) <= 20:
                assessment.rejection_reason = "Maximum stop distance must be between 0 and 20 percent"
                return assessment
            effective_max_stop_pct = min(
                effective_max_stop_pct,
                float(max_stop_distance_pct) / 100.0,
            )
        if atr_pct is not None:
            if not math.isfinite(float(atr_pct)) or not 0 < float(atr_pct) <= 20:
                assessment.rejection_reason = "ATR percent must be between 0 and 20 percent"
                return assessment
            atr_stop_pct = (float(atr_pct) / 100.0) * self.ATR_STOP_MULTIPLIER
            effective_max_stop_pct = min(
                self.SL_MAX_PCT,
                max(effective_max_stop_pct, atr_stop_pct),
            )
            assessment.atr_pct = float(atr_pct)
            assessment.stop_distance_atr = sl_pct / (float(atr_pct) / 100.0)
            assessment.stop_policy = "atr_aware_candidate_seed"
        assessment.stop_distance_pct = sl_pct * 100.0
        assessment.max_stop_distance_pct = effective_max_stop_pct * 100.0
        if sl_pct > effective_max_stop_pct:
            assessment.sl_valid = False
            assessment.rejection_reason = (
                f"SL is too wide ({sl_pct*100:.2f}% of entry; max {effective_max_stop_pct*100:.2f}%)"
            )
            return assessment

        effective_min_stop_pct = self.SL_MIN_PCT
        if min_stop_distance_pct is not None:
            if not math.isfinite(float(min_stop_distance_pct)) or float(min_stop_distance_pct) <= 0:
                assessment.rejection_reason = "Minimum stop distance must be positive"
                return assessment
            effective_min_stop_pct = max(effective_min_stop_pct, float(min_stop_distance_pct) / 100.0)

        if sl_pct < effective_min_stop_pct:
            assessment.sl_valid = False
            assessment.approved = False
            assessment.rejection_reason = (
                f"SL is too tight ({sl_pct*100:.3f}% of entry; min {effective_min_stop_pct*100:.2f}%); "
                "market noise and execution costs exceed structural risk margin"
            )
            return assessment

        if execution_cost_per_unit > 0 and sl_dist > 0:
            fee_to_risk = execution_cost_per_unit / sl_dist
            if fee_to_risk > self.MAX_FEE_TO_RISK_RATIO:
                assessment.approved = False
                assessment.rejection_reason = (
                    f"Execution cost too high relative to SL distance "
                    f"({fee_to_risk*100:.1f}% > {self.MAX_FEE_TO_RISK_RATIO*100:.0f}%)"
                )
                return assessment

        # --- 5. R:R calculation (observability only) ---
        # ExecutionRevalidator already enforced the minimum executable R:R
        # threshold before this stage runs.  RiskEngine records the calculated
        # value and sets rr_ok for downstream display but does NOT hard-reject
        # here to avoid a double-gate with the identical threshold.
        # The safety floor (MIN_RR) is used only when no minimum_rr was passed
        # in — i.e. when RiskEngine is called directly in tests / standalone.
        all_in_risk_dist = max(sl_dist + execution_cost_per_unit, 1e-9)
        net_reward_dist = max(
            abs(signal.take_profit - signal.entry) - execution_cost_per_unit,
            0.0,
        )
        calculated_rr = net_reward_dist / all_in_risk_dist
        assessment.executable_rr = round(calculated_rr, 4)
        assessment.risk_reward = assessment.executable_rr
        assessment.execution_cost_per_unit = round(execution_cost_per_unit, 10)
        if minimum_rr is not None:
            if not math.isfinite(float(minimum_rr)) or not 1 <= float(minimum_rr) <= 20:
                assessment.rejection_reason = "Minimum R:R must be between 1 and 20"
                return assessment
        effective_min_rr = (
            float(minimum_rr) if minimum_rr is not None else self.MIN_RR
        )
        if not math.isfinite(effective_min_rr):
            assessment.rejection_reason = "Minimum executable R:R must be a finite number"
            return assessment
        assessment.minimum_executable_rr = round(effective_min_rr, 4)
        if calculated_rr < effective_min_rr:
            assessment.rr_ok = False
            # Only hard-reject when called standalone (no minimum_rr passed in).
            # In the auto-pilot pipeline, ExecutionRevalidator is the R:R gate owner.
            if minimum_rr is None:
                assessment.approved = False
                assessment.rejection_reason = (
                    f"R:R too low ({calculated_rr:.2f} < {effective_min_rr})"
                )
                return assessment
            assessment.warnings.append(
                f"R:R {calculated_rr:.2f} below floor {effective_min_rr:.2f}"
                " (already validated by ExecutionRevalidator)"
            )


        # --- 6. Position sizing ---
        if risk_per_trade_pct is not None and (
            not math.isfinite(float(risk_per_trade_pct)) or not 0 < float(risk_per_trade_pct) <= 5
        ):
            assessment.rejection_reason = "Risk per trade override must be between 0 and 5 percent"
            return assessment
        base_risk_pct = self._adjust_risk(
            drawdown_pct,
            assessment,
            base_override=risk_per_trade_pct,
        )
        assessment.risk_budget_source = (
            "deterministic_config_sqz"
            if bool(getattr(self.cfg, "sqz_risk_scaling_enabled", False))
            else "deterministic_config"
        )
        assessment.base_risk_pct = base_risk_pct
        risk_pct = base_risk_pct * assessment.regime_risk_multiplier * assessment.cluster_risk_multiplier
        if assessment.regime_risk_multiplier < 1.0 or assessment.cluster_risk_multiplier < 1.0:
            assessment.tone = "cautious"
            if assessment.regime_risk_multiplier < 1.0:
                assessment.warnings.append(
                    f"{assessment.market_regime.title()} regime — risk reduced to "
                    f"{assessment.regime_risk_multiplier:.0%} of budget"
                )
        risk_budget = account_balance * (risk_pct / 100)
        
        # Instrument-aware sizing with an aggregate portfolio leverage ceiling.
        max_notional = account_balance * max_leverage
        assessment.current_gross_notional = round(current_gross_notional, 8)
        assessment.max_portfolio_notional = round(max_notional, 8)
        if current_gross_notional >= max_notional - 1e-9:
            assessment.portfolio_notional_ok = False
            assessment.rejection_reason = (
                f"Aggregate portfolio notional limit reached "
                f"({current_gross_notional:.2f} >= {max_notional:.2f})"
            )
            return assessment
        remaining_portfolio_notional = max(max_notional - current_gross_notional, 0.0)
        max_units = remaining_portfolio_notional / (signal.entry * contract_multiplier)
        raw_size = risk_budget / (all_in_risk_dist * contract_multiplier)
        capped_size = min(raw_size, max_units)
        step_d = Decimal(str(quantity_step))
        capped_d = Decimal(str(round(capped_size, 8)))
        steps = (capped_d / step_d).to_integral_value(rounding=ROUND_FLOOR)
        step_exp = step_d.as_tuple().exponent
        step_decimals = abs(step_exp) if isinstance(step_exp, int) and step_exp < 0 else 0
        position_size = round(float(steps * step_d), step_decimals)
        if position_size <= 0:
            assessment.rejection_reason = "Account is too small for the instrument quantity step"
            return assessment

        new_position_notional = (
            position_size * signal.entry * contract_multiplier
        )
        portfolio_gross_notional = current_gross_notional + new_position_notional
        assessment.current_gross_notional = round(current_gross_notional, 8)
        assessment.new_position_notional = round(new_position_notional, 8)
        assessment.portfolio_gross_notional = round(portfolio_gross_notional, 8)
        assessment.max_portfolio_notional = round(max_notional, 8)
        if portfolio_gross_notional > max_notional + 1e-9:
            assessment.portfolio_notional_ok = False
            assessment.rejection_reason = (
                f"Aggregate portfolio notional exceeds leverage cap "
                f"({portfolio_gross_notional:.2f} > {max_notional:.2f})"
            )
            return assessment

        actual_risk = position_size * all_in_risk_dist * contract_multiplier

        assessment.risk_pct = round(actual_risk / account_balance * 100.0, 4)
        assessment.risk_amount = round(actual_risk, 2)
        assessment.position_size = position_size

        # --- 7. Portfolio correlation warning ---
        if open_positions >= max(1, self.cfg.max_open_positions // 2):
            assessment.warnings.append(
                f"Portfolio concentration: {open_positions} positions open — watch correlation"
            )

        assessment.approved = True
        logger.info(
            f"[Risk] APPROVED {signal.symbol} ({cluster}) | size={assessment.position_size} "
            f"risk={assessment.risk_pct:.2f}% (cluster_mult={cluster_multiplier}) rr={calculated_rr:.2f}"
        )
        return assessment

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _adjust_risk(
        self,
        drawdown_pct: float,
        assessment: RiskAssessment,
        *,
        base_override: float | None = None,
    ) -> float:
        """
        Dynamically adjust risk percentage based on current drawdown.
        """
        base = (
            float(base_override)
            if base_override is not None
            else self.cfg.default_risk_per_trade
        )

        if drawdown_pct >= 10:
            assessment.tone = "cautious"
            adjusted = min(base * self.DD_10_MULTIPLIER, self.DD_10_MAX_RISK_CAP_PCT)
            assessment.warnings.append(
                f"Drawdown {drawdown_pct:.1f}% — reducing risk to {adjusted:.2f}%"
            )
            return adjusted
        elif drawdown_pct >= 5:
            assessment.tone = "cautious"
            adjusted = min(base * self.DD_5_MULTIPLIER, self.DD_5_MAX_RISK_CAP_PCT)
            assessment.warnings.append(
                f"Drawdown {drawdown_pct:.1f}% — reducing risk to {adjusted:.2f}%"
            )
            return adjusted
        elif drawdown_pct > 0:
            assessment.tone = "cautious"
            return round(base * self.DD_LIGHT_MULTIPLIER, 2)
        else:
            assessment.tone = "normal"
            return base
