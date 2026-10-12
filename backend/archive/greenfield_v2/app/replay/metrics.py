"""
Replay Metrics Engine (README.md Section 43)
Calculates institutional-grade performance and execution metrics from simulation runs.
"""

from dataclasses import dataclass, field
from typing import Any
import numpy as np


@dataclass
class FamilyMetrics:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    profit_factor: float = 0.0
    total_r: float = 0.0


@dataclass
class StateMetrics:
    total_opportunities: int = 0
    executed_trades: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0


@dataclass
class ExecutionMetrics:
    market_entries: int = 0
    limit_entries: int = 0
    limit_orders_placed: int = 0
    limit_orders_filled: int = 0
    limit_orders_expired: int = 0
    limit_orders_cancelled: int = 0
    limit_fill_rate: float = 0.0
    missed_limit_rate: float = 0.0
    average_limit_wait_bars: float = 0.0


@dataclass
class ReplayMetrics:
    total_opportunities: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    profit_factor: float = 0.0
    total_realized_r: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_r: float = 0.0
    average_holding_bars: float = 0.0
    
    execution: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    by_family: dict[str, FamilyMetrics] = field(default_factory=dict)
    by_state: dict[str, StateMetrics] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_opportunities": self.total_opportunities,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "breakeven_trades": self.breakeven_trades,
            "win_rate": round(self.win_rate, 4),
            "expectancy_r": round(self.expectancy_r, 4),
            "profit_factor": round(self.profit_factor, 4),
            "total_realized_r": round(self.total_realized_r, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "max_drawdown_r": round(self.max_drawdown_r, 4),
            "average_holding_bars": round(self.average_holding_bars, 2),
            "execution": {
                "market_entries": self.execution.market_entries,
                "limit_entries": self.execution.limit_entries,
                "limit_orders_placed": self.execution.limit_orders_placed,
                "limit_orders_filled": self.execution.limit_orders_filled,
                "limit_orders_expired": self.execution.limit_orders_expired,
                "limit_orders_cancelled": self.execution.limit_orders_cancelled,
                "limit_fill_rate": round(self.execution.limit_fill_rate, 4),
                "missed_limit_rate": round(self.execution.missed_limit_rate, 4),
                "average_limit_wait_bars": round(self.execution.average_limit_wait_bars, 2),
            },
            "by_family": {k: {
                "total_trades": v.total_trades,
                "wins": v.wins,
                "losses": v.losses,
                "win_rate": round(v.win_rate, 4),
                "expectancy_r": round(v.expectancy_r, 4),
                "profit_factor": round(v.profit_factor, 4),
                "total_r": round(v.total_r, 4),
            } for k, v in self.by_family.items()},
            "by_state": {k: {
                "total_opportunities": v.total_opportunities,
                "executed_trades": v.executed_trades,
                "win_rate": round(v.win_rate, 4),
                "expectancy_r": round(v.expectancy_r, 4),
            } for k, v in self.by_state.items()},
        }


def calculate_replay_metrics(
    closed_trades: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]] | None = None,
) -> ReplayMetrics:
    """Calculates all Section 43 replay metrics deterministically."""
    total_trades = len(closed_trades)
    total_opps = len(opportunities)

    if total_trades == 0:
        return ReplayMetrics(
            total_opportunities=total_opps,
            total_trades=0,
            execution=ExecutionMetrics(
                limit_orders_placed=len([o for o in orders if o.get("order_type") == "limit"]),
                market_entries=len([o for o in orders if o.get("order_type") == "market" and o.get("status") == "FILLED"]),
            )
        )

    r_multiples = [float(t.get("realized_r", 0.0)) for t in closed_trades]
    wins = [r for r in r_multiples if r > 0.05]
    losses = [r for r in r_multiples if r < -0.05]
    bes = [r for r in r_multiples if -0.05 <= r <= 0.05]

    win_count = len(wins)
    loss_count = len(losses)
    be_count = len(bes)
    win_rate = win_count / total_trades if total_trades > 0 else 0.0

    total_r = sum(r_multiples)
    expectancy_r = np.mean(r_multiples) if r_multiples else 0.0

    gross_profit_r = sum(wins)
    gross_loss_r = abs(sum(losses))
    profit_factor = gross_profit_r / gross_loss_r if gross_loss_r > 0 else (99.0 if gross_profit_r > 0 else 0.0)

    # Max Drawdown R
    peak_r = 0.0
    cum_r = 0.0
    max_dd_r = 0.0
    for r in r_multiples:
        cum_r += r
        if cum_r > peak_r:
            peak_r = cum_r
        dd = peak_r - cum_r
        if dd > max_dd_r:
            max_dd_r = dd

    # Max Drawdown % from equity curve
    max_dd_pct = 0.0
    if equity_curve and len(equity_curve) > 1:
        peak_eq = equity_curve[0].get("equity", 10000.0)
        for pt in equity_curve:
            eq = pt.get("equity", 10000.0)
            if eq > peak_eq:
                peak_eq = eq
            if peak_eq > 0:
                dd_pct = (peak_eq - eq) / peak_eq * 100.0
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct

    holding_bars = [int(t.get("holding_bars", 1)) for t in closed_trades]
    avg_holding = float(np.mean(holding_bars)) if holding_bars else 0.0

    # Execution Metrics
    limit_placed = [o for o in orders if o.get("order_type") == "limit"]
    market_placed = [o for o in orders if o.get("order_type") == "market"]
    limit_filled = [o for o in limit_placed if o.get("status") == "FILLED"]
    limit_expired = [o for o in limit_placed if o.get("status") == "EXPIRED"]
    limit_cancelled = [o for o in limit_placed if o.get("status") == "CANCELLED"]

    n_limit_placed = len(limit_placed)
    n_limit_filled = len(limit_filled)
    fill_rate = n_limit_filled / n_limit_placed if n_limit_placed > 0 else 0.0
    missed_rate = (len(limit_expired) + len(limit_cancelled)) / n_limit_placed if n_limit_placed > 0 else 0.0

    wait_bars = [int(o.get("wait_bars", 0)) for o in limit_filled]
    avg_wait_bars = float(np.mean(wait_bars)) if wait_bars else 0.0

    exec_metrics = ExecutionMetrics(
        market_entries=len(market_placed),
        limit_entries=n_limit_filled,
        limit_orders_placed=n_limit_placed,
        limit_orders_filled=n_limit_filled,
        limit_orders_expired=len(limit_expired),
        limit_orders_cancelled=len(limit_cancelled),
        limit_fill_rate=fill_rate,
        missed_limit_rate=missed_rate,
        average_limit_wait_bars=avg_wait_bars,
    )

    # Breakdown by Family
    by_family: dict[str, FamilyMetrics] = {}
    families = set(t.get("strategy_family", "unknown") for t in closed_trades)
    for fam in families:
        fam_trades = [t for t in closed_trades if t.get("strategy_family") == fam]
        fam_rs = [float(t.get("realized_r", 0.0)) for t in fam_trades]
        f_wins = [r for r in fam_rs if r > 0.05]
        f_losses = [r for r in fam_rs if r < -0.05]
        f_gp = sum(f_wins)
        f_gl = abs(sum(f_losses))
        f_pf = f_gp / f_gl if f_gl > 0 else (99.0 if f_gp > 0 else 0.0)
        by_family[fam] = FamilyMetrics(
            total_trades=len(fam_trades),
            wins=len(f_wins),
            losses=len(f_losses),
            win_rate=len(f_wins) / len(fam_trades) if fam_trades else 0.0,
            expectancy_r=float(np.mean(fam_rs)) if fam_rs else 0.0,
            profit_factor=f_pf,
            total_r=sum(fam_rs),
        )

    # Breakdown by State
    by_state: dict[str, StateMetrics] = {}
    states = set(o.get("market_state", "unknown") for o in opportunities)
    for st in states:
        st_opps = [o for o in opportunities if o.get("market_state") == st]
        st_trades = [t for t in closed_trades if t.get("market_state") == st]
        st_rs = [float(t.get("realized_r", 0.0)) for t in st_trades]
        st_wins = [r for r in st_rs if r > 0.05]
        by_state[st] = StateMetrics(
            total_opportunities=len(st_opps),
            executed_trades=len(st_trades),
            win_rate=len(st_wins) / len(st_trades) if st_trades else 0.0,
            expectancy_r=float(np.mean(st_rs)) if st_rs else 0.0,
        )

    return ReplayMetrics(
        total_opportunities=total_opps,
        total_trades=total_trades,
        winning_trades=win_count,
        losing_trades=loss_count,
        breakeven_trades=be_count,
        win_rate=win_rate,
        expectancy_r=expectancy_r,
        profit_factor=profit_factor,
        total_realized_r=total_r,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_r=max_dd_r,
        average_holding_bars=avg_holding,
        execution=exec_metrics,
        by_family=by_family,
        by_state=by_state,
    )
