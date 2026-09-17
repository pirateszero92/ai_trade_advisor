"""
Strategy Engine
Loads strategy rules from config/strategy.yaml and evaluates SMCSignals
against those rules to produce a StrategyResult with a go/no-go decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import math
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from app.core.strategy_config_store import read_strategy_config
from app.engines.smc_engine import SMCSignal

STRATEGY_FILE = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"

DEFAULT_STRATEGY: dict[str, Any] = {
    "name": "SMC Default",
    "version": "1.0",
    "filters": {
        "min_confluence": 60,
        "require_indicator_readiness": False,
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
    setup_direction: Literal["long", "short", "wait"] = "wait"
    strategy_name: str = ""
    rejection_reasons: list[str] = field(default_factory=list)
    passed_checks: list[str] = field(default_factory=list)
    override_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
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
            "override_reasons": self.override_reasons,
            "warnings": self.warnings,
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

    Rules are loaded from ``config/strategy.yaml``. If the file is missing or
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
        """Approve only a deterministic causal Tri-Core plan.

        Regime, HMM, VPIN, inducement and legacy Scenario are retained as
        analytics/risk context; they no longer veto an otherwise valid entry.
        """
        raw_direction = signal.direction if signal.direction in {"long", "short"} else "wait"
        result = StrategyResult(
            strategy_name=self._strategy.get("name", "Unknown"),
            setup_direction=raw_direction,
        )
        filters = deepcopy(self._strategy.get("filters", DEFAULT_STRATEGY["filters"]))
        override = self._strategy.get("symbol_overrides", {}).get(signal.symbol, {})
        filters.update(override.get("filters", {}))
        setup = getattr(signal, "tri_core_setup", {}) or {}
        if setup.get("actionable") is not True:
            reasons = setup.get("rejection_reasons") or ["No causal SMC+CVD setup"]
            result.rejection_reasons.extend(str(reason) for reason in reasons[:3])
            return result
        direction = str(setup.get("direction", "wait"))
        if direction not in filters.get("allowed_directions", ["long", "short"]):
            result.rejection_reasons.append(f"Direction {direction} not allowed")
            return result
        if not setup.get("smc_confirmed") or not setup.get("flow_confirmed"):
            result.rejection_reasons.append("Both causal SMC and trusted aggressor-flow confirmation are required")
        if getattr(signal, "volume_quality", "unavailable") != "exchange_aggressor":
            result.rejection_reasons.append("Trusted exchange-derived aggressor CVD is required")
        levels = (signal.entry, signal.stop_loss, signal.take_profit)
        if any(level is None or not math.isfinite(float(level)) or float(level) <= 0 for level in levels):
            result.rejection_reasons.append("Structural entry, invalidation and target are required")
        elif direction == "long" and not signal.stop_loss < signal.entry < signal.take_profit:
            result.rejection_reasons.append("Invalid LONG geometry")
        elif direction == "short" and not signal.take_profit < signal.entry < signal.stop_loss:
            result.rejection_reasons.append("Invalid SHORT geometry")
        min_rr = float(filters.get("min_rr", 2.0))
        if float(getattr(signal, "risk_reward", 0.0)) < min_rr:
            result.rejection_reasons.append(f"R:R {signal.risk_reward:.2f} < minimum {min_rr:.2f}")

        regime = getattr(signal, "market_regime", {}) or {}
        policy = regime.get("policy", {}) if isinstance(regime, dict) else {}
        result.market_regime = str(regime.get("regime", "unknown"))
        result.effective_policy = deepcopy(policy) if isinstance(policy, dict) else {}
        result.effective_policy.setdefault("min_confluence", 65.0)
        result.effective_policy.setdefault("min_rr", min_rr)
        result.effective_policy.setdefault("risk_multiplier", 1.0)
        for label, value in (
            ("VPIN toxicity", (getattr(signal, "order_flow", {}) or {}).get("toxic_flow_detected")),
            ("Inducement nearby", getattr(signal, "active_zone_type", "") == "inducement_trap"),
            ("Crowded derivatives", (getattr(signal, "derivatives_sentiment", {}) or {}).get("sentiment_bias") in {"CROWDED_LONG", "CROWDED_SHORT"}),
        ):
            if value:
                result.warnings.append(label)
        result.score = int(getattr(signal, "confluence_score", getattr(signal, "confluence", 0)))
        result.passed_checks.extend(["Causal SMC setup confirmed", "Exchange-derived CVD confirmation aligned",
                                     f"Structural R:R {signal.risk_reward:.2f} OK"])
        result.direction = direction if not result.rejection_reasons else "wait"
        result.approved = not result.rejection_reasons
        return result

    def reload(self) -> None:
        self._strategy = self._load_strategy()
        logger.info(f"[Strategy] Reloaded: {self._strategy.get('name')}")

    # ------------------------------------------------------------------
    # Architectural Note:
    # In the canonical architecture, TriCoreSetupEngine (tri_core_engine.py)
    # is the sole deterministic gating authority. Legacy heuristic filters
    # (_apply_regime_policy, _check_long, _check_short, _apply_universal_filters)
    # were pruned to maintain clear boundaries. Informational matrix
    # filters remain housed in mtf_analysis.py for research visualization.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Loader
    # ------------------------------------------------------------------

    @staticmethod
    def _load_strategy() -> dict:
        try:
            if STRATEGY_FILE.exists():
                data = read_strategy_config(STRATEGY_FILE)
                if isinstance(data, dict) and "filters" in data and "long_conditions" in data and "short_conditions" in data:
                    filters = data.get("filters", {})
                    min_confluence = filters.get("min_confluence", 55)
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
