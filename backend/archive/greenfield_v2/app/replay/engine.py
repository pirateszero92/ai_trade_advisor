"""
Historical Replay Engine (README.md Section 42)
Executes bar-by-bar historical replay using ReplayClock and DecisionOrchestrator.
Guarantees strictly zero future-candle leakage and deterministically reproduces:
- closed-candle facts
- setup detection
- opportunity creation & lifecycle
- execution decisions
- pending limit evolution
- fill simulation
- risk allocation & portfolio rules
- position management (auto break-even, stop loss, take profit)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import pandas as pd

from app.domain.clock import ReplayClock
from app.domain.opportunity import OpportunityState
from app.domain.order import OrderStatus
from app.domain.position import Position, PositionStatus
from app.domain.reason_codes import ReasonCode
from app.market_data.quote import MarketQuote
from app.orchestrator import DecisionOrchestrator
from app.replay.counterfactual import CounterfactualRecord, CounterfactualSimulator, CounterfactualSummary
from app.replay.metrics import ReplayMetrics, calculate_replay_metrics


@dataclass
class ReplayResult:
    symbol: str
    timeframe: str
    total_bars_replayed: int
    initial_equity: float
    final_equity: float
    equity_curve: list[dict[str, Any]]
    closed_trades: list[dict[str, Any]]
    opportunities: list[dict[str, Any]]
    metrics: ReplayMetrics
    counterfactual: CounterfactualSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "total_bars_replayed": self.total_bars_replayed,
            "initial_equity": self.initial_equity,
            "final_equity": round(self.final_equity, 2),
            "total_return_pct": round((self.final_equity - self.initial_equity) / self.initial_equity * 100, 2),
            "metrics": self.metrics.to_dict(),
            "counterfactual": self.counterfactual.to_dict(),
            "total_closed_trades": len(self.closed_trades),
            "total_opportunities": len(self.opportunities),
        }


class ReplayEngine:
    """Historical bar-by-bar deterministic trading simulator."""

    def __init__(
        self,
        symbol: str,
        historical_df: pd.DataFrame,
        timeframe: str = "15m",
        initial_equity: float = 10_000.0,
        warmup_bars: int = 25,
        max_daily_loss_pct: float = 3.0,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe.lower()
        self.initial_equity = initial_equity
        self.current_equity = initial_equity
        self.warmup_bars = max(20, warmup_bars)
        self.max_daily_loss_pct = max_daily_loss_pct

        # Ensure datetime index
        self.df = historical_df.copy()
        if not isinstance(self.df.index, pd.DatetimeIndex):
            if "timestamp" in self.df.columns:
                self.df["timestamp"] = pd.to_datetime(self.df["timestamp"], utc=True)
                self.df = self.df.set_index("timestamp")
            else:
                self.df.index = pd.to_datetime(self.df.index, utc=True)
        if self.df.index.tz is None:
            self.df.index = self.df.index.tz_localize("UTC")
        else:
            self.df.index = self.df.index.tz_convert("UTC")

        # Timeframe duration
        self.tf_delta = pd.Timedelta(self.timeframe.replace("m", "min"))

    def run(self) -> ReplayResult:
        """Executes the full historical replay bar-by-bar."""
        total_bars = len(self.df)
        if total_bars <= self.warmup_bars:
            raise ValueError(
                f"Historical frame has {total_bars} bars, but at least {self.warmup_bars + 1} are required."
            )

        start_time = self.df.index[self.warmup_bars] + self.tf_delta
        clock = ReplayClock(start_time.to_pydatetime())
        from app.risk.allocator import RiskAllocator
        risk_alloc = RiskAllocator(max_daily_loss_pct=self.max_daily_loss_pct)
        orchestrator = DecisionOrchestrator(clock=clock, risk_allocator=risk_alloc)

        equity_curve: list[dict[str, Any]] = []
        closed_trades: list[dict[str, Any]] = []
        all_opportunities: list[dict[str, Any]] = []
        all_orders: list[dict[str, Any]] = []
        cf_records: list[CounterfactualRecord] = []
        active_positions: list[Position] = []

        daily_pnl_pct = 0.0

        for bar_idx in range(self.warmup_bars, total_bars):
            bar = self.df.iloc[bar_idx]
            bar_open_time = self.df.index[bar_idx]
            bar_close_time = bar_open_time + self.tf_delta
            current_dt = bar_close_time.to_pydatetime()
            clock.set_time(current_dt)

            bar_open = float(bar["open"])
            bar_high = float(bar["high"])
            bar_low = float(bar["low"])
            bar_close = float(bar["close"])

            quote = MarketQuote(
                symbol=self.symbol,
                bid=bar_close,
                ask=bar_close,
                last=bar_close,
                timestamp=current_dt,
            )

            # ------------------------------------------------------------------
            # 1. Check Pending Limit Orders on OMS (Simulate Fills)
            # ------------------------------------------------------------------
            for order in list(orchestrator.oms.orders.values()):
                if order.symbol == self.symbol and order.status == OrderStatus.PENDING and order.limit_price:
                    l_price = order.limit_price
                    filled = False
                    fill_price = l_price

                    if order.side.value.lower() == "buy" and bar_low <= l_price:
                        filled = True
                        fill_price = min(bar_open, l_price)
                    elif order.side.value.lower() == "sell" and bar_high >= l_price:
                        filled = True
                        fill_price = max(bar_open, l_price)

                    if filled:
                        orchestrator.oms.fill_order(order.id, fill_price=fill_price, filled_quantity=order.quantity)
                        # Open new Position
                        opp = orchestrator.store.get(order.opportunity_id)
                        pos = Position(
                            symbol=order.symbol,
                            direction=opp.direction.value if opp else ("long" if order.side.value == "buy" else "short"),
                            average_entry_price=fill_price,
                            quantity=order.quantity,
                            initial_stop_loss=opp.stop_loss if opp else fill_price * 0.98,
                            current_stop_loss=opp.stop_loss if opp else fill_price * 0.98,
                            take_profit=opp.take_profit if opp else fill_price * 1.05,
                            order_id=order.id,
                            opportunity_id=order.opportunity_id,
                            opened_at=current_dt,
                            bars_held=0,
                        )
                        active_positions.append(pos)
                        if opp:
                            opp.transition_to(OpportunityState.FILLED, ReasonCode.OMS_FILL)

            # ------------------------------------------------------------------
            # 2. Manage Active Positions (Check SL, TP, Auto Break-Even)
            # ------------------------------------------------------------------
            still_active: list[Position] = []
            for pos in active_positions:
                pos.bars_held += 1
                is_long = pos.direction.lower() == "long"
                initial_risk_dist = abs(pos.average_entry_price - pos.initial_stop_loss)

                # Check auto break-even progression
                favorable_price = bar_high if is_long else bar_low
                dist = favorable_price - pos.average_entry_price if is_long else pos.average_entry_price - favorable_price
                if initial_risk_dist > 0 and dist >= initial_risk_dist:
                    pos.advance_protection(ReasonCode.BREAK_EVEN_TRIGGERED)

                # Check SL hit
                sl_hit = (is_long and bar_low <= pos.current_stop_loss) or (not is_long and bar_high >= pos.current_stop_loss)
                # Check TP hit
                tp_hit = (is_long and bar_high >= pos.take_profit) or (not is_long and bar_low <= pos.take_profit)

                if sl_hit:
                    exit_price = pos.current_stop_loss
                    pnl = (exit_price - pos.average_entry_price) * pos.quantity if is_long else (pos.average_entry_price - exit_price) * pos.quantity
                    realized_r = pnl / (initial_risk_dist * pos.quantity) if initial_risk_dist > 0 else -1.0
                    pos.close(exit_price=exit_price, net_pnl=pnl, reason=ReasonCode.STOP_LOSS_HIT)
                    self.current_equity += pnl
                    closed_trades.append({
                        "opportunity_id": pos.opportunity_id,
                        "symbol": pos.symbol,
                        "direction": pos.direction,
                        "entry": pos.average_entry_price,
                        "exit": exit_price,
                        "pnl": round(pnl, 2),
                        "realized_r": round(realized_r, 2),
                        "holding_bars": pos.bars_held,
                        "exit_reason": "STOP_LOSS",
                    })
                elif tp_hit:
                    exit_price = pos.take_profit
                    pnl = (exit_price - pos.average_entry_price) * pos.quantity if is_long else (pos.average_entry_price - exit_price) * pos.quantity
                    realized_r = pnl / (initial_risk_dist * pos.quantity) if initial_risk_dist > 0 else 2.0
                    pos.close(exit_price=exit_price, net_pnl=pnl, reason=ReasonCode.TAKE_PROFIT_HIT)
                    self.current_equity += pnl
                    closed_trades.append({
                        "opportunity_id": pos.opportunity_id,
                        "symbol": pos.symbol,
                        "direction": pos.direction,
                        "entry": pos.average_entry_price,
                        "exit": exit_price,
                        "pnl": round(pnl, 2),
                        "realized_r": round(realized_r, 2),
                        "holding_bars": pos.bars_held,
                        "exit_reason": "TAKE_PROFIT",
                    })
                else:
                    still_active.append(pos)

            active_positions = still_active

            # ------------------------------------------------------------------
            # 3. Decision Cycle on Closed Candle
            # ------------------------------------------------------------------
            sliced_df = self.df.iloc[: bar_idx + 1]
            cycle_res = orchestrator.run_cycle(
                symbol=self.symbol,
                raw_ohlcv=sliced_df,
                quote=quote,
                account_equity=self.current_equity,
                daily_pnl_pct=daily_pnl_pct,
                timeframe=self.timeframe,
            )

            opp = cycle_res.get("opportunity")
            if opp:
                opp_dict = opp.model_dump(mode="json")
                all_opportunities.append(opp_dict)
                # Counterfactual analysis on newly detected opportunity
                cf_rec = CounterfactualSimulator.evaluate_opportunity(
                    opp=opp_dict,
                    start_bar_idx=bar_idx,
                    df=self.df,
                )
                cf_records.append(cf_rec)

            order = cycle_res.get("order")
            if order:
                all_orders.append(order.model_dump(mode="json"))

            # Equity curve tracking
            equity_curve.append({
                "bar_idx": bar_idx,
                "timestamp": current_dt.isoformat(),
                "equity": round(self.current_equity, 2),
                "open_positions": len(active_positions),
            })

        # Calculate metrics
        metrics = calculate_replay_metrics(
            closed_trades=closed_trades,
            opportunities=all_opportunities,
            orders=all_orders,
            equity_curve=equity_curve,
        )
        cf_summary = CounterfactualSimulator.summarize(cf_records)

        return ReplayResult(
            symbol=self.symbol,
            timeframe=self.timeframe,
            total_bars_replayed=total_bars - self.warmup_bars,
            initial_equity=self.initial_equity,
            final_equity=self.current_equity,
            equity_curve=equity_curve,
            closed_trades=closed_trades,
            opportunities=all_opportunities,
            metrics=metrics,
            counterfactual=cf_summary,
        )
