"""
Strategy Engine
Loads strategy rules from config/strategy.yaml and evaluates SMCSignals
against those rules to produce a StrategyResult with a go/no-go decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml
from loguru import logger

from app.engines.smc_engine import SMCSignal

STRATEGY_FILE = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"

DEFAULT_STRATEGY: dict[str, Any] = {
    "name": "SMC Default",
    "version": "1.0",
    "filters": {
        "min_confluence": 65,
        "require_indicator_readiness": False,
        "require_bos": False,
        "require_ob": True,
        "require_fvg": False,
        "require_liquidity_sweep": False,
        "min_rr": 1.5,       # Aligned with RiskEngine.MIN_RR (was 2.0, risk_engine uses 1.5)
        "allowed_directions": ["long", "short"],
        "htf_alignment_required": False,
    },
    "long_conditions": {
        "bias_must_be": ["bullish", "neutral"],
        "price_zone": "discount_or_eq",
        "ob_direction": "bullish",
    },
    "short_conditions": {
        "bias_must_be": ["bearish", "neutral"],
        "price_zone": "premium_or_eq",
        "ob_direction": "bearish",
    },
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class StrategyResult:
    """Outcome of strategy rule evaluation."""
    approved: bool = False
    direction: Literal["long", "short", "wait"] = "wait"
    # ``direction`` is the executable action after all gates.  Keep the raw
    # setup direction separately so clients can say "bullish setup, WAIT"
    # without silently turning market bias into an approved order.
    setup_direction: Literal["long", "short", "wait"] = "wait"
    strategy_name: str = ""
    rejection_reasons: list[str] = field(default_factory=list)
    passed_checks: list[str] = field(default_factory=list)
    score: int = 0
    market_regime: str = "legacy"
    effective_policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "status": "ready" if self.approved else "wait",
            "direction": self.direction,
            "setup_direction": self.setup_direction,
            "strategy_name": self.strategy_name,
            "rejection_reasons": self.rejection_reasons,
            "passed_checks": self.passed_checks,
            "score": self.score,
            "market_regime": self.market_regime,
            "effective_policy": self.effective_policy,
        }


# ---------------------------------------------------------------------------
# Strategy Engine
# ---------------------------------------------------------------------------

class StrategyEngine:
    """
    Evaluates trade signals against configurable strategy rules.

    Rules are loaded from ``config/strategy.yaml``.  If the file is missing or
    malformed, the built-in ``DEFAULT_STRATEGY`` is used as a fallback.
    """

    def __init__(self, strategy_config: dict[str, Any] | None = None):
        self._strategy: dict[str, Any] = deepcopy(
            strategy_config if strategy_config is not None else self._load_strategy()
        )

    @property
    def config_snapshot(self) -> dict[str, Any]:
        """Return an isolated snapshot suitable for Phase 3 evidence/replay."""
        return deepcopy(self._strategy)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, signal: SMCSignal) -> StrategyResult:
        """
        Evaluate a signal against strategy rules.

        Parameters
        ----------
        signal:
            Populated SMCSignal from the SMC engine.

        Returns
        -------
        StrategyResult
        """
        raw_direction = signal.direction if signal.direction in {"long", "short"} else "wait"
        result = StrategyResult(
            strategy_name=self._strategy.get("name", "Unknown"),
            setup_direction=raw_direction,
        )
        filters = deepcopy(self._strategy.get("filters", DEFAULT_STRATEGY["filters"]))
        override = self._strategy.get("symbol_overrides", {}).get(signal.symbol, {})
        filters.update(override.get("filters", {}))

        # Direction gate
        if signal.direction == "wait":
            result.rejection_reasons.append("No trade direction identified")
            return result

        direction = signal.direction
        if direction not in filters.get("allowed_directions", ["long", "short"]):
            result.rejection_reasons.append(f"Direction {direction} not allowed by strategy")
            return result

        self._apply_regime_policy(signal, filters, result)

        # Run direction-specific checks
        if direction == "long":
            self._check_long(signal, filters, result)
        else:
            self._check_short(signal, filters, result)

        # Universal filters
        self._apply_universal_filters(signal, filters, result)

        result.direction = direction if not result.rejection_reasons else "wait"
        result.approved = len(result.rejection_reasons) == 0
        return result

    def reload(self) -> None:
        """Reload strategy rules from disk."""
        self._strategy = self._load_strategy()
        logger.info(f"[Strategy] Reloaded: {self._strategy.get('name')}")

    # ------------------------------------------------------------------
    # Direction checks
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_regime_policy(
        signal: SMCSignal, filters: dict[str, Any], result: StrategyResult
    ) -> None:
        """Tighten base filters for the classified environment.

        The policy can only make the base strategy more conservative. It does
        not lower the configured confluence or R:R thresholds.
        """
        regime_data = getattr(signal, "market_regime", {})
        if not isinstance(regime_data, dict) or not regime_data:
            return
        regime = str(regime_data.get("regime", "unknown"))
        policy = regime_data.get("policy", {})
        if not isinstance(policy, dict) or not policy:
            result.market_regime = regime
            result.rejection_reasons.append("Market regime policy is unavailable")
            return

        result.market_regime = regime
        filters["min_confluence"] = max(
            float(filters.get("min_confluence", 65)),
            float(policy.get("min_confluence", 100)),
        )
        filters["min_rr"] = max(
            float(filters.get("min_rr", 1.5)),
            float(policy.get("min_rr", 20)),
        )
        for flag in (
            "require_liquidity_sweep",
            "require_volume_confirmation",
            "require_squeeze_fire",
        ):
            filters[flag] = bool(filters.get(flag, False) or policy.get(flag, False))
        result.effective_policy = {
            **deepcopy(policy),
            "min_confluence": filters["min_confluence"],
            "min_rr": filters["min_rr"],
            "require_liquidity_sweep": filters["require_liquidity_sweep"],
            "require_volume_confirmation": filters["require_volume_confirmation"],
            "require_squeeze_fire": filters["require_squeeze_fire"],
        }

        if not regime_data.get("ready", False):
            result.rejection_reasons.append("Market regime data is not ready")
        if not policy.get("entry_allowed", False):
            result.rejection_reasons.append(
                f"New entries are blocked in {regime} regime"
            )
        else:
            result.passed_checks.append(f"{regime.title()} regime allows selective entries")

        if policy.get("require_direction_alignment", False):
            regime_direction = regime_data.get("direction", "neutral")
            expected = "bullish" if signal.direction == "long" else "bearish"
            if regime_direction != expected:
                result.rejection_reasons.append(
                    f"Trade direction is not aligned with {regime} regime ({regime_direction})"
                )
            else:
                result.passed_checks.append("Trade direction aligns with market regime")

    def _check_long(
        self, signal: SMCSignal, filters: dict, result: StrategyResult
    ) -> None:
        """Apply long-specific strategy rules."""
        cond = deepcopy(self._strategy.get("long_conditions", DEFAULT_STRATEGY["long_conditions"]))
        cond.update(
            self._strategy.get("symbol_overrides", {})
            .get(signal.symbol, {})
            .get("long_conditions", {})
        )
        allowed_bias = cond.get("bias_must_be", ["bullish", "neutral"])
        if signal.bias not in allowed_bias and signal.htf_bias not in allowed_bias:
            result.rejection_reasons.append(
                f"Long requires bias in {allowed_bias}, got bias={signal.bias}"
            )
        else:
            result.passed_checks.append("Bullish bias confirmed")
            result.score += 1

        zone_rule = cond.get("price_zone", "discount_or_eq")
        if zone_rule == "discount_only" and not signal.in_discount:
            result.rejection_reasons.append("Long requires price in discount zone")
        elif zone_rule == "discount_or_eq" and signal.in_premium:
            result.rejection_reasons.append("Long rejected: price in premium zone")
        else:
            result.passed_checks.append("Price zone OK for long")
            result.score += 1

        ob_dir = cond.get("ob_direction", "bullish")
        if filters.get("require_ob") and (
            signal.order_block is None or signal.order_block.direction != ob_dir
        ):
            result.rejection_reasons.append(f"Long requires {ob_dir} order block")
        elif signal.order_block and signal.order_block.direction == ob_dir:
            result.passed_checks.append("Bullish OB present")
            result.score += 2

    def _check_short(
        self, signal: SMCSignal, filters: dict, result: StrategyResult
    ) -> None:
        """Apply short-specific strategy rules."""
        cond = deepcopy(self._strategy.get("short_conditions", DEFAULT_STRATEGY["short_conditions"]))
        cond.update(
            self._strategy.get("symbol_overrides", {})
            .get(signal.symbol, {})
            .get("short_conditions", {})
        )
        allowed_bias = cond.get("bias_must_be", ["bearish", "neutral"])
        if signal.bias not in allowed_bias and signal.htf_bias not in allowed_bias:
            result.rejection_reasons.append(
                f"Short requires bias in {allowed_bias}, got bias={signal.bias}"
            )
        else:
            result.passed_checks.append("Bearish bias confirmed")
            result.score += 1

        zone_rule = cond.get("price_zone", "premium_or_eq")
        if zone_rule == "premium_only" and not signal.in_premium:
            result.rejection_reasons.append("Short requires price in premium zone")
        elif zone_rule == "premium_or_eq" and signal.in_discount:
            result.rejection_reasons.append("Short rejected: price in discount zone")
        else:
            result.passed_checks.append("Price zone OK for short")
            result.score += 1

        ob_dir = cond.get("ob_direction", "bearish")
        if filters.get("require_ob") and (
            signal.order_block is None or signal.order_block.direction != ob_dir
        ):
            result.rejection_reasons.append(f"Short requires {ob_dir} order block")
        elif signal.order_block and signal.order_block.direction == ob_dir:
            result.passed_checks.append("Bearish OB present")
            result.score += 2

    def _apply_universal_filters(
        self, signal: SMCSignal, filters: dict, result: StrategyResult
    ) -> None:
        """Apply filters that apply to both long and short trades."""
        indicator_decision = getattr(signal, "indicator_decision", {})
        if filters.get("require_indicator_readiness") and indicator_decision:
            if not indicator_decision.get("ready", False):
                reasons = indicator_decision.get("blocking_reasons", [])
                detail = "; ".join(str(reason) for reason in reasons)
                result.rejection_reasons.append(
                    f"Indicator data is not ready: {detail or 'coverage check failed'}"
                )
            else:
                coverage = float(indicator_decision.get("coverage", 0.0))
                result.passed_checks.append(
                    f"Indicator data coverage {coverage:.1f}% OK"
                )
                result.score += 1

        min_conf = filters.get("min_confluence", 65)
        conf = getattr(signal, "confluence_score", getattr(signal, "confluence", 0))
        if conf < min_conf:
            result.rejection_reasons.append(
                f"Confluence {conf} < minimum {min_conf}"
            )
        else:
            result.passed_checks.append(f"Confluence {conf}/{min_conf} OK")
            result.score += 1

        if filters.get("require_bos") and not signal.bos:
            result.rejection_reasons.append("BOS required but not detected")
        elif signal.bos:
            result.passed_checks.append("BOS confirmed")
            result.score += 1

        if filters.get("require_fvg") and signal.fvg is None:
            result.rejection_reasons.append("FVG required but not detected")
        elif signal.fvg:
            result.passed_checks.append("FVG present")
            result.score += 1

        if filters.get("require_liquidity_sweep") and not signal.liquidity_swept:
            result.rejection_reasons.append("Liquidity sweep required but not detected")
        elif signal.liquidity_swept:
            result.passed_checks.append("Liquidity swept")
            result.score += 1

        if filters.get("require_volume_confirmation"):
            volume_valid = getattr(signal, "volume_data_valid", False)
            delta = float(getattr(signal, "volume_delta", 0.0))
            absorption_type = getattr(signal, "delta_absorption_type", None)
            aligned = (
                signal.direction == "long"
                and (delta > 0 or absorption_type == "bullish_absorption")
            ) or (
                signal.direction == "short"
                and (delta < 0 or absorption_type == "bearish_absorption")
            )
            if not volume_valid or not aligned:
                result.rejection_reasons.append(
                    "Direction-aligned Volume Delta confirmation is required"
                )
            else:
                result.passed_checks.append("Volume Delta confirms volatile-regime entry")
                result.score += 1

        if filters.get("require_squeeze_fire"):
            if not getattr(signal, "squeeze_data_valid", False) or signal.squeeze_status != "squeeze_fire":
                result.rejection_reasons.append("Squeeze release is required before entry")
            else:
                result.passed_checks.append("Squeeze release confirmed")
                result.score += 1

        min_rr = filters.get("min_rr", 1.5)
        if signal.risk_reward < min_rr:
            result.rejection_reasons.append(
                f"R:R {signal.risk_reward:.2f} < minimum {min_rr}"
            )
        else:
            result.passed_checks.append(f"R:R {signal.risk_reward:.2f} OK")
            result.score += 1

        if filters.get("htf_alignment_required"):
            if signal.htf_bias != "neutral" and signal.htf_bias != signal.bias:
                result.rejection_reasons.append(
                    f"HTF ({signal.htf_bias}) / LTF ({signal.bias}) misaligned"
                )
            else:
                result.passed_checks.append("HTF/LTF aligned")
                result.score += 1

    # ------------------------------------------------------------------
    # Loader
    # ------------------------------------------------------------------

    @staticmethod
    def _load_strategy() -> dict:
        try:
            if STRATEGY_FILE.exists():
                with open(STRATEGY_FILE, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict) and "filters" in data and "long_conditions" in data and "short_conditions" in data:
                    filters = data.get("filters", {})
                    min_confluence = filters.get("min_confluence", 65)
                    min_rr = filters.get("min_rr", 1.5)
                    require_readiness = filters.get(
                        "require_indicator_readiness", False
                    )
                    if not isinstance(min_confluence, (int, float)) or not 0 <= min_confluence <= 100:
                        raise ValueError("filters.min_confluence must be between 0 and 100")
                    if not isinstance(min_rr, (int, float)) or min_rr < 1:
                        raise ValueError("filters.min_rr must be at least 1")
                    if not isinstance(require_readiness, bool):
                        raise ValueError(
                            "filters.require_indicator_readiness must be boolean"
                        )
                    logger.info(f"[Strategy] Loaded: {data.get('name', 'Unnamed')}")
                    return data
        except Exception as exc:
            logger.warning(f"[Strategy] Could not load strategy.yaml: {exc}")
        return DEFAULT_STRATEGY
