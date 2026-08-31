"""
Monte Carlo Simulation & Risk of Ruin Engine
Performs bootstrap resampling of trade sequences to evaluate equity curve stability,
tail-risk distribution (VaR / CVaR), and true probability of ruin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import numpy as np


@dataclass
class MonteCarloResult:
    iterations: int
    trades_per_path: int
    initial_capital: float
    probability_of_ruin_pct: Optional[float]
    ruin_threshold_pct: float          # Drawdown % defined as ruin (default 40%)
    median_max_drawdown_pct: Optional[float]
    var_95_max_drawdown_pct: Optional[float]
    var_99_max_drawdown_pct: Optional[float]
    median_terminal_equity: Optional[float]
    percentile_5_terminal_equity: Optional[float]
    percentile_95_terminal_equity: Optional[float]
    equity_curve_percentiles: dict[str, list[float]] = field(default_factory=dict)
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "iterations": self.iterations,
            "trades_per_path": self.trades_per_path,
            "initial_capital": self.initial_capital,
            "probability_of_ruin_pct": self.probability_of_ruin_pct,
            "ruin_threshold_pct": self.ruin_threshold_pct,
            "median_max_drawdown_pct": self.median_max_drawdown_pct,
            "var_95_max_drawdown_pct": self.var_95_max_drawdown_pct,
            "var_99_max_drawdown_pct": self.var_99_max_drawdown_pct,
            "median_terminal_equity": self.median_terminal_equity,
            "percentile_5_terminal_equity": self.percentile_5_terminal_equity,
            "percentile_95_terminal_equity": self.percentile_95_terminal_equity,
            "equity_curve_percentiles": self.equity_curve_percentiles,
            "status": self.status,
        }


class MonteCarloEngine:
    """Bootstrap resampler for trade sequence stress testing."""

    @staticmethod
    def simulate(
        trade_pnls: list[float],
        *,
        initial_capital: float = 10_000.0,
        iterations: int = 10_000,
        trades_per_path: int = 100,
        ruin_drawdown_pct: float = 40.0,
        seed: int = 42,
    ) -> MonteCarloResult:
        """
        Run Monte Carlo bootstrap simulation on historical trade P&L values.
        """
        if initial_capital <= 0 or iterations < 1 or trades_per_path < 1 or not 0 < ruin_drawdown_pct <= 100:
            raise ValueError("Invalid Monte Carlo simulation parameters")
        if not trade_pnls or len(trade_pnls) < 5:
            return MonteCarloResult(
                iterations=iterations,
                trades_per_path=trades_per_path,
                initial_capital=initial_capital,
                probability_of_ruin_pct=None,
                ruin_threshold_pct=ruin_drawdown_pct,
                median_max_drawdown_pct=None,
                var_95_max_drawdown_pct=None,
                var_99_max_drawdown_pct=None,
                median_terminal_equity=None,
                percentile_5_terminal_equity=None,
                percentile_95_terminal_equity=None,
                status="insufficient_data",
            )

        rng = np.random.default_rng(seed)
        pnl_array = np.array(trade_pnls, dtype=float)
        if not np.isfinite(pnl_array).all():
            raise ValueError("trade_pnls contains NaN or infinite values")
        n_samples = len(pnl_array)

        # Generate bootstrap sample matrix (iterations x trades_per_path)
        sampled_indices = rng.integers(0, n_samples, size=(iterations, trades_per_path))
        sampled_pnls = pnl_array[sampled_indices]

        # Compute equity curves
        cum_pnls = np.cumsum(sampled_pnls, axis=1)
        equity_paths = np.hstack([np.full((iterations, 1), initial_capital), initial_capital + cum_pnls])

        # Compute running peaks and drawdowns
        running_peaks = np.maximum.accumulate(equity_paths, axis=1)
        drawdowns_pct = (running_peaks - equity_paths) / np.maximum(running_peaks, 1e-6) * 100.0
        max_drawdowns_per_path = np.max(drawdowns_pct, axis=1)

        # Terminal equities
        terminal_equities = equity_paths[:, -1]

        # Ruin analysis
        ruined_paths = np.sum(max_drawdowns_per_path >= ruin_drawdown_pct)
        prob_ruin = float((ruined_paths / iterations) * 100.0)

        # Percentiles
        median_dd = float(np.median(max_drawdowns_per_path))
        var_95_dd = float(np.percentile(max_drawdowns_per_path, 95))
        var_99_dd = float(np.percentile(max_drawdowns_per_path, 99))

        median_term = float(np.median(terminal_equities))
        p5_term = float(np.percentile(terminal_equities, 5))
        p95_term = float(np.percentile(terminal_equities, 95))

        # Sample 10 equidistant step points for chart visualization
        step_indices = np.linspace(0, trades_per_path, 11, dtype=int)
        curve_percentiles = {
            "p5": [float(np.percentile(equity_paths[:, idx], 5)) for idx in step_indices],
            "p25": [float(np.percentile(equity_paths[:, idx], 25)) for idx in step_indices],
            "p50": [float(np.percentile(equity_paths[:, idx], 50)) for idx in step_indices],
            "p75": [float(np.percentile(equity_paths[:, idx], 75)) for idx in step_indices],
            "p95": [float(np.percentile(equity_paths[:, idx], 95)) for idx in step_indices],
        }

        return MonteCarloResult(
            iterations=iterations,
            trades_per_path=trades_per_path,
            initial_capital=initial_capital,
            probability_of_ruin_pct=round(prob_ruin, 2),
            ruin_threshold_pct=ruin_drawdown_pct,
            median_max_drawdown_pct=round(median_dd, 2),
            var_95_max_drawdown_pct=round(var_95_dd, 2),
            var_99_max_drawdown_pct=round(var_99_dd, 2),
            median_terminal_equity=round(median_term, 2),
            percentile_5_terminal_equity=round(p5_term, 2),
            percentile_95_terminal_equity=round(p95_term, 2),
            equity_curve_percentiles=curve_percentiles,
        )
