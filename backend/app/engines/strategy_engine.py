"""
Strategy Engine
Loads strategy rules from config/strategy.yaml and evaluates SMCSignals
against those rules to produce a StrategyResult with a go/no-go decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from loguru import logger

from app.engines.smc_engine import SMCSignal

STRATEGY_FILE = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"

DEFAULT_STRATEGY: dict[str, Any] = {
    "name": "SMC Default",
    "version": "1.0",
    "filters": {
        "min_confluence": 65,
        "require_bos": False,
        "require_ob": True,
        "require_fvg": False,
        "require_liquidity_sweep": False,
        "min_rr": 2.0,
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
    strategy_name: str = ""
    rejection_reasons: list[str] = field(default_factory=list)
    passed_checks: list[str] = field(default_factory=list)
    score: int = 0

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "direction": self.direction,
            "strategy_name": self.strategy_name,
            "rejection_reasons": self.rejection_reasons,
            "passed_checks": self.passed_checks,
            "score": self.score,
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

    def __init__(self):
        self._strategy: dict[str, Any] = self._load_strategy()

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
        result = StrategyResult(strategy_name=self._strategy.get("name", "Unknown"))
        filters = self._strategy.get("filters", DEFAULT_STRATEGY["filters"])

        # Direction gate
        if signal.direction == "wait":
            result.rejection_reasons.append("No trade direction identified")
            return result

        direction = signal.direction
        if direction not in filters.get("allowed_directions", ["long", "short"]):
            result.rejection_reasons.append(f"Direction {direction} not allowed by strategy")
            return result

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

    def _check_long(
        self, signal: SMCSignal, filters: dict, result: StrategyResult
    ) -> None:
        """Apply long-specific strategy rules."""
        cond = self._strategy.get("long_conditions", DEFAULT_STRATEGY["long_conditions"])
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
        cond = self._strategy.get("short_conditions", DEFAULT_STRATEGY["short_conditions"])
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
                if isinstance(data, dict) and ("long" in data or "long_conditions" in data or "entry_rules" in data):
                    logger.info(f"[Strategy] Loaded: {data.get('name', 'Unnamed')}")
                    return data
        except Exception as exc:
            logger.warning(f"[Strategy] Could not load strategy.yaml: {exc}")
        return DEFAULT_STRATEGY
