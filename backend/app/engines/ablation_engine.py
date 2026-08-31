"""
Ablation Testing Engine
Quantifies the marginal alpha contribution of each indicator layer
(SMC Only vs SMC+CVD vs SMC+SQZ vs Full Trinity) on identical historical datasets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any, Optional
import pandas as pd

from app.engines.indicator_core import validate_indicator_core_config

from app.engines.backtest_engine import (
    ExecutionAssumptions,
    run_walk_forward_backtest,
)


@dataclass
class AblationVariantResult:
    variant_name: str
    description: str
    completed_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    expectancy_r: float
    profit_factor: float
    net_pnl: float
    net_return_pct: float
    max_drawdown_pct: float
    marginal_expectancy_delta_r: float = 0.0  # Difference vs SMC_ONLY baseline
    marginal_win_rate_delta_pct: float = 0.0


@dataclass
class AblationStudySummary:
    symbol: str
    timeframe: str
    data_bars: int
    baseline_variant: str
    best_variant: str
    variants: dict[str, AblationVariantResult] = field(default_factory=dict)
    key_findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "data_bars": self.data_bars,
            "baseline_variant": self.baseline_variant,
            "best_variant": self.best_variant,
            "variants": {k: v.__dict__ for k, v in self.variants.items()},
            "key_findings": list(self.key_findings),
        }


class AblationEngine:
    """Runs 4-way orthogonal indicator ablation studies."""

    VARIANTS = {
        "SMC_ONLY": {
            "name": "SMC Only (Where)",
            "description": "Baseline pure structural Price Action & Order Blocks",
            "indicator_core": {
                "version": 1,
                "minimum_data_coverage": 70.0,
                "indicators": {
                    "smc_structure": {"enabled": True, "required": True, "weight": 100.0, "params": {}},
                    "volume_delta": {"enabled": False, "required": False, "weight": 0.0, "params": {}},
                    "squeeze_momentum": {"enabled": False, "required": False, "weight": 0.0, "params": {}},
                },
            },
        },
        "SMC_CVD": {
            "name": "SMC + CVD (Where + Intent)",
            "description": "Structure confirmed by Order Flow Delta Absorption",
            "indicator_core": {
                "version": 1,
                "minimum_data_coverage": 70.0,
                "indicators": {
                    "smc_structure": {"enabled": True, "required": True, "weight": 60.0, "params": {}},
                    "volume_delta": {"enabled": True, "required": False, "weight": 40.0, "params": {}},
                    "squeeze_momentum": {"enabled": False, "required": False, "weight": 0.0, "params": {}},
                },
            },
        },
        "SMC_SQZ": {
            "name": "SMC + SQZ (Where + When)",
            "description": "Structure filtered by Squeeze Volatility Timing",
            "indicator_core": {
                "version": 1,
                "minimum_data_coverage": 70.0,
                "indicators": {
                    "smc_structure": {"enabled": True, "required": True, "weight": 60.0, "params": {}},
                    "volume_delta": {"enabled": False, "required": False, "weight": 0.0, "params": {}},
                    "squeeze_momentum": {"enabled": True, "required": False, "weight": 40.0, "params": {}},
                },
            },
        },
        "FULL_TRINITY": {
            "name": "Full Trinity (Where + Intent + When)",
            "description": "Complete 3-Layer Confluence Architecture",
            "indicator_core": {
                "version": 1,
                "minimum_data_coverage": 70.0,
                "indicators": {
                    "smc_structure": {"enabled": True, "required": True, "weight": 40.0, "params": {}},
                    "volume_delta": {"enabled": True, "required": False, "weight": 30.0, "params": {}},
                    "squeeze_momentum": {"enabled": True, "required": False, "weight": 30.0, "params": {}},
                },
            },
        },
    }

    def run_study(
        self,
        *,
        market_data: pd.DataFrame,
        symbol: str,
        timeframe: str,
        base_config: dict[str, Any],
        assumptions: Optional[ExecutionAssumptions] = None,
        initial_capital: float = 10_000.0,
        risk_per_trade_pct: float = 1.0,
        oos_fraction: float = 0.70,
    ) -> AblationStudySummary:
        """Run all 4 ablation variants and compute marginal statistical contribution."""
        if market_data is None or market_data.empty:
            raise ValueError("Ablation study requires non-empty market data")

        exec_assumptions = assumptions or ExecutionAssumptions()
        variant_results: dict[str, AblationVariantResult] = {}

        validated_variants = {
            key: validate_indicator_core_config(value["indicator_core"])
            for key, value in self.VARIANTS.items()
        }

        for var_key, var_cfg in self.VARIANTS.items():
            config = deepcopy(base_config)
            config["indicator_core"] = deepcopy(validated_variants[var_key])

            bt_res = run_walk_forward_backtest(
                market_data=market_data,
                symbol=symbol,
                timeframe=timeframe,
                config=config,
                assumptions=exec_assumptions,
                initial_capital=initial_capital,
                risk_per_trade_pct=risk_per_trade_pct,
                oos_fraction=oos_fraction,
            )

            metrics = bt_res["metrics"]
            variant_results[var_key] = AblationVariantResult(
                variant_name=var_cfg["name"],
                description=var_cfg["description"],
                completed_trades=int(metrics["completed_trades"]),
                wins=int(metrics["wins"]),
                losses=int(metrics["losses"]),
                win_rate_pct=round(float(metrics["win_rate_pct"]), 2),
                expectancy_r=round(float(metrics["expectancy_r"]), 4),
                profit_factor=round(float(metrics["profit_factor"] or 0.0), 2),
                net_pnl=round(float(metrics["net_pnl"]), 2),
                net_return_pct=round(float(metrics["net_return_pct"]), 2),
                max_drawdown_pct=round(float(metrics["max_drawdown_pct"]), 2),
            )

        # Baseline comparison
        base = variant_results["SMC_ONLY"]
        for key, res in variant_results.items():
            if key != "SMC_ONLY":
                res.marginal_expectancy_delta_r = round(res.expectancy_r - base.expectancy_r, 4)
                res.marginal_win_rate_delta_pct = round(res.win_rate_pct - base.win_rate_pct, 2)

        # Find best variant by Expectancy R (or Profit Factor)
        best_key = max(variant_results, key=lambda k: (variant_results[k].expectancy_r, variant_results[k].profit_factor))

        # Generate findings
        findings: list[str] = []
        full = variant_results["FULL_TRINITY"]
        if full.expectancy_r > base.expectancy_r:
            findings.append(
                f"Full Trinity outperforms baseline by +{full.marginal_expectancy_delta_r:.4f}R expectancy "
                f"(Win Rate: {base.win_rate_pct}% -> {full.win_rate_pct}%)"
            )
        cvd = variant_results["SMC_CVD"]
        if cvd.expectancy_r > base.expectancy_r:
            findings.append(f"Volume Delta (CVD) provides +{cvd.marginal_expectancy_delta_r:.4f}R marginal alpha over pure SMC.")
        sqz = variant_results["SMC_SQZ"]
        if sqz.expectancy_r > base.expectancy_r:
            findings.append(f"Squeeze Momentum (SQZ) timing provides +{sqz.marginal_expectancy_delta_r:.4f}R marginal alpha over pure SMC.")

        return AblationStudySummary(
            symbol=symbol,
            timeframe=timeframe,
            data_bars=len(market_data),
            baseline_variant="SMC_ONLY",
            best_variant=best_key,
            variants=variant_results,
            key_findings=findings,
        )
