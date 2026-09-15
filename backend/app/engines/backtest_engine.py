"""Deterministic Phase 3 execution simulator, OOS backtest and release gate."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from app.engines.risk_engine import RiskEngine, squeeze_adjusted_risk_pct
from app.engines.timeframe_profiles import validate_timeframe_profiles
from app.services.execution_analysis import analyze_execution_frame

_RESAMPLE_RULES = {
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "1d": "1D",
    "1w": "1W",
}


def _resample_closed_history(
    history: pd.DataFrame,
    *,
    target_timeframe: str,
    trigger_timeframe: str,
) -> pd.DataFrame:
    """Aggregate completed trigger candles without leaking an open HTF bar."""
    rule = _RESAMPLE_RULES.get(target_timeframe)
    if not rule or not isinstance(history.index, pd.DatetimeIndex):
        return pd.DataFrame()
    aggregation: dict[str, str] = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    for column in ("volume", "buy_volume", "sell_volume", "volume_delta"):
        if column in history:
            aggregation[column] = "sum"
    if "cvd" in history:
        aggregation["cvd"] = "last"
    if "flow_source" in history:
        aggregation["flow_source"] = "last"
    aggregated = history[list(aggregation)].resample(
        rule,
        label="right",
        closed="left",
        origin="epoch",
    ).agg(aggregation).dropna(subset=["open", "high", "low", "close"])
    decision_close = pd.Timestamp(history.index[-1]) + pd.to_timedelta(trigger_timeframe)
    return aggregated[aggregated.index <= decision_close]


@dataclass(frozen=True)
class ExecutionAssumptions:
    fee_bps: float = 10.0
    spread_bps: float = 5.0
    slippage_bps: float = 3.0
    latency_bars: int = 1
    entry_timeout_bars: int = 3
    max_holding_bars: int = 24
    max_volume_participation: float = 0.01
    max_fill_fraction_per_bar: float = 0.50
    zero_volume_fill_fraction: float = 0.25

    def __post_init__(self) -> None:
        for name in ("fee_bps", "spread_bps", "slippage_bps"):
            value = float(getattr(self, name))
            if not 0 <= value <= 500:
                raise ValueError(f"{name} must be between 0 and 500 bps")
        if not 0 <= self.latency_bars <= 20:
            raise ValueError("latency_bars must be between 0 and 20")
        if not 1 <= self.entry_timeout_bars <= 100:
            raise ValueError("entry_timeout_bars must be between 1 and 100")
        if not 1 <= self.max_holding_bars <= 1000:
            raise ValueError("max_holding_bars must be between 1 and 1000")
        for name in (
            "max_volume_participation",
            "max_fill_fraction_per_bar",
            "zero_volume_fill_fraction",
        ):
            value = float(getattr(self, name))
            if not 0 < value <= 1:
                raise ValueError(f"{name} must be greater than 0 and at most 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReleaseCriteria:
    min_completed_trades: int = 100
    min_trades_per_scenario: int = 20
    min_expectancy_r: float = 0.05
    min_profit_factor: float = 1.15
    max_drawdown_pct: float = 12.0
    min_fill_rate: float = 0.70
    min_regimes_tested: int = 2
    require_out_of_sample: bool = True
    min_true_aggressor_coverage: float = 0.95
    min_history_days: float = 90.0
    require_validated_policy: bool = True
    min_oos_folds: int = 3

    def __post_init__(self) -> None:
        if not 1 <= self.min_completed_trades <= 100_000:
            raise ValueError("min_completed_trades must be positive")
        if not 1 <= self.min_trades_per_scenario <= self.min_completed_trades:
            raise ValueError("min_trades_per_scenario must be positive and not exceed total minimum")
        if not -5 <= self.min_expectancy_r <= 10:
            raise ValueError("min_expectancy_r is outside the safe range")
        if not 0 <= self.min_profit_factor <= 100:
            raise ValueError("min_profit_factor is outside the safe range")
        if not 0 < self.max_drawdown_pct <= 100:
            raise ValueError("max_drawdown_pct must be between 0 and 100")
        if not 0 <= self.min_fill_rate <= 1:
            raise ValueError("min_fill_rate must be between 0 and 1")
        if not 1 <= self.min_regimes_tested <= 10:
            raise ValueError("min_regimes_tested must be between 1 and 10")
        if not 0 < self.min_true_aggressor_coverage <= 1:
            raise ValueError("min_true_aggressor_coverage must be between 0 and 1")
        if not 1 <= self.min_history_days <= 3650:
            raise ValueError("min_history_days must be between 1 and 3650")
        if not 1 <= self.min_oos_folds <= 20:
            raise ValueError("min_oos_folds must be between 1 and 20")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_aggressor_data_readiness(frame: pd.DataFrame) -> dict[str, Any]:
    """Audit whether replay rows contain trustworthy, reconciling trade flow."""
    required = {"volume", "buy_volume", "sell_volume", "flow_source"}
    missing = sorted(required - set(frame.columns)) if frame is not None else sorted(required)
    if frame is None or frame.empty or missing:
        return {
            "ready_for_replay": False,
            "ready_for_promotion": False,
            "coverage": 0.0,
            "history_days": 0.0,
            "missing_columns": missing,
            "reason": "Exchange-derived aggressor columns are unavailable",
        }
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    buy = pd.to_numeric(frame["buy_volume"], errors="coerce")
    sell = pd.to_numeric(frame["sell_volume"], errors="coerce")
    source = frame["flow_source"].astype(str).str.lower()
    trusted_source = source.str.contains("binance") & (
        source.str.contains("taker") | source.str.contains("aggressor")
    )
    finite_non_negative = volume.notna() & buy.notna() & sell.notna() & (
        (volume >= 0) & (buy >= 0) & (sell >= 0)
    )
    tolerance = volume.abs().mul(0.02).clip(lower=1e-9)
    reconciles = (buy + sell - volume).abs() <= tolerance
    valid = trusted_source & finite_non_negative & reconciles
    coverage = float(valid.mean()) if len(valid) else 0.0
    history_days = 0.0
    if isinstance(frame.index, pd.DatetimeIndex) and len(frame) > 1:
        history_days = max(
            0.0,
            (pd.Timestamp(frame.index[-1]) - pd.Timestamp(frame.index[0])).total_seconds()
            / 86400.0,
        )
    ready_for_replay = coverage >= 0.95
    return {
        "ready_for_replay": ready_for_replay,
        "ready_for_promotion": ready_for_replay and history_days >= 90.0,
        "coverage": round(coverage, 6),
        "history_days": round(history_days, 3),
        "valid_rows": int(valid.sum()),
        "total_rows": len(frame),
        "missing_columns": [],
        "reason": (
            "ready"
            if ready_for_replay
            else "Aggressor rows are missing, untrusted, or fail volume reconciliation"
        ),
    }


def summarize_sweep_observations(
    observations: list[dict[str, Any]],
    *,
    split_scope: str,
) -> dict[str, Any]:
    """Describe event timing without selecting a production threshold."""
    def distribution(field: str) -> dict[str, float | int | None]:
        values = pd.Series([
            float(item[field])
            for item in observations
            if item.get(field) is not None and math.isfinite(float(item[field]))
        ], dtype="float64")
        if values.empty:
            return {"count": 0, "p50": None, "p75": None, "p90": None}
        return {
            "count": len(values),
            "p50": round(float(values.quantile(0.50)), 4),
            "p75": round(float(values.quantile(0.75)), 4),
            "p90": round(float(values.quantile(0.90)), 4),
        }

    return {
        "split_scope": split_scope,
        "observation_count": len(observations),
        "reclaim_duration_bars": distribution("reclaim_duration_bars"),
        "post_reclaim_extension_atr": distribution("post_reclaim_extension_atr"),
        "candidate_selection": "not_selected",
        "warning": (
            "Percentiles describe the sample only. Select thresholds on training/inner-validation "
            "utility, then freeze them before outer OOS evaluation."
        ),
    }


def _execution_price(reference: float, direction: str, leg: str, assumptions: ExecutionAssumptions) -> float:
    half_spread = assumptions.spread_bps / 20_000.0
    slippage = assumptions.slippage_bps / 10_000.0
    is_buy = (direction == "long" and leg == "entry") or (
        direction == "short" and leg == "exit"
    )
    multiplier = 1.0 + half_spread + slippage if is_buy else 1.0 - half_spread - slippage
    return max(reference * multiplier, 1e-12)


def _bar_capacity(bar: pd.Series, requested_quantity: float, assumptions: ExecutionAssumptions) -> float:
    volume = float(bar.get("volume", 0.0) or 0.0)
    volume_capacity = (
        volume * assumptions.max_volume_participation
        if math.isfinite(volume) and volume > 0
        else requested_quantity * assumptions.zero_volume_fill_fraction
    )
    return max(
        0.0,
        min(
            volume_capacity,
            requested_quantity * assumptions.max_fill_fraction_per_bar,
        ),
    )


def simulate_execution(
    *,
    direction: str,
    order_type: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    requested_quantity: float,
    future_bars: pd.DataFrame,
    assumptions: ExecutionAssumptions,
    market_regime: str = "unknown",
) -> dict[str, Any]:
    """Simulate entry, costs, partial fills and conservative SL/TP ordering."""
    if direction not in {"long", "short"}:
        raise ValueError("direction must be long or short")
    if order_type not in {"market", "limit"}:
        raise ValueError("order_type must be market or limit")
    if requested_quantity <= 0 or not math.isfinite(requested_quantity):
        raise ValueError("requested_quantity must be positive and finite")
    if entry <= 0 or stop_loss <= 0 or take_profit <= 0:
        raise ValueError("entry, stop_loss and take_profit must be positive")
    geometry_ok = (
        stop_loss < entry < take_profit
        if direction == "long"
        else take_profit < entry < stop_loss
    )
    if not geometry_ok:
        raise ValueError("Invalid trade geometry")
    if future_bars is None or future_bars.empty:
        return {
            "status": "unfilled",
            "requested_quantity": requested_quantity,
            "filled_quantity": 0.0,
            "fill_rate": 0.0,
            "fills": [],
            "exit_offset": 0,
        }

    bars = future_bars.copy()
    bars.columns = [str(column).lower() for column in bars.columns]
    entry_fills: list[dict[str, Any]] = []
    remaining = requested_quantity
    start_offset = assumptions.latency_bars
    if start_offset >= len(bars):
        return {
            "status": "unfilled",
            "requested_quantity": requested_quantity,
            "filled_quantity": 0.0,
            "fill_rate": 0.0,
            "fills": [],
            "exit_offset": len(bars) - 1,
        }
    entry_deadline = min(len(bars), start_offset + assumptions.entry_timeout_bars)
    last_entry_offset = start_offset
    first_entry_offset: int | None = None
    exit_reason: str | None = None
    exit_reference: float | None = None
    exit_offset: int | None = None

    active_stop = stop_loss

    def protective_exit(bar: pd.Series, allow_tp: bool = True) -> tuple[str, float] | None:
        """Return conservative, gap-aware execution reference for one bar."""
        open_price = float(bar["open"])
        if direction == "long":
            if float(bar["low"]) <= active_stop:
                return "stop_loss", min(active_stop, open_price)
            if allow_tp and float(bar["high"]) >= take_profit:
                return "take_profit", max(take_profit, open_price)
        else:
            if float(bar["high"]) >= active_stop:
                return "stop_loss", max(active_stop, open_price)
            if allow_tp and float(bar["low"]) <= take_profit:
                return "take_profit", min(take_profit, open_price)
        return None

    for offset in range(start_offset, entry_deadline):
        bar = bars.iloc[offset]

        # Quantity filled on an earlier bar is already exposed. A terminal
        # event cancels the unfilled remainder before any new fill this bar.
        if entry_fills:
            triggered = protective_exit(bar)
            if triggered is not None:
                exit_reason, exit_reference = triggered
                exit_offset = offset
                break

        touched = order_type == "market"
        if order_type == "limit":
            touched = (
                float(bar["low"]) <= entry
                if direction == "long"
                else float(bar["high"]) >= entry
            )
        if not touched:
            continue
        capacity = _bar_capacity(bar, requested_quantity, assumptions)
        quantity = min(remaining, capacity)
        if quantity <= 0:
            continue
        reference = float(bar["open"]) if order_type == "market" else entry
        price = _execution_price(reference, direction, "entry", assumptions)
        if order_type == "limit":
            # Maker limit fills cannot execute worse than the limit price
            price = min(price, entry) if direction == "long" else max(price, entry)
        fee = abs(price * quantity) * assumptions.fee_bps / 10_000.0
        entry_fills.append(
            {
                "leg": "entry",
                "bar_offset": offset,
                "timestamp": pd.Timestamp(bars.index[offset]).isoformat(),
                "reference_price": reference,
                "price": price,
                "quantity": quantity,
                "fee": fee,
                "spread_cost": abs(reference * assumptions.spread_bps / 20_000.0 * quantity),
                "slippage_cost": abs(reference * assumptions.slippage_bps / 10_000.0 * quantity),
                "liquidity": "taker" if order_type == "market" else "maker_simulated",
            }
        )
        remaining -= quantity
        first_entry_offset = offset if first_entry_offset is None else first_entry_offset
        last_entry_offset = offset

        # The new fill is exposed for the rest of this bar. Stop-first ordering
        # is conservative when OHLC data cannot reveal the intrabar sequence.
        # For limit entries, do not assume favorable same-bar TP because the peak
        # might have occurred before the limit price was touched.
        triggered = protective_exit(bar, allow_tp=(order_type != "limit"))
        if triggered is not None:
            exit_reason, exit_reference = triggered
            exit_offset = offset
            break
        if remaining <= requested_quantity * 1e-12:
            break

    filled_quantity = requested_quantity - remaining
    if filled_quantity <= 0:
        return {
            "status": "unfilled",
            "requested_quantity": requested_quantity,
            "filled_quantity": 0.0,
            "fill_rate": 0.0,
            "fills": [],
            "exit_offset": start_offset,
        }

    average_entry = sum(fill["price"] * fill["quantity"] for fill in entry_fills) / filled_quantity
    entry_fees = sum(fill["fee"] for fill in entry_fills)
    risk_per_unit = abs(average_entry - stop_loss)
    if risk_per_unit <= 0:
        raise ValueError("Execution costs produced an invalid risk distance")

    assert first_entry_offset is not None
    if exit_reason is None:
        monitor_start = last_entry_offset + 1 if remaining <= requested_quantity * 1e-12 else entry_deadline
        holding_end = min(len(bars), first_entry_offset + assumptions.max_holding_bars + 1)
        default_offset = min(max(last_entry_offset, monitor_start - 1), holding_end - 1)
        exit_reason = "end_of_data"
        exit_offset = default_offset
        exit_reference = float(bars.iloc[default_offset]["close"])
        favorable_extreme = average_entry
        for offset in range(monitor_start, holding_end):
            bar = bars.iloc[offset]
            triggered = protective_exit(bar)
            exit_offset = offset
            if triggered is not None:
                exit_reason, exit_reference = triggered
                break
            exit_reference = float(bar["close"])
            # OHLC cannot reveal intrabar sequencing, so protection advances
            # only after this bar and becomes executable on the next bar.
            favorable_extreme = (
                max(favorable_extreme, float(bar["high"]))
                if direction == "long"
                else min(favorable_extreme, float(bar["low"]))
            )
            r_multiple_seen = (
                (favorable_extreme - average_entry) / risk_per_unit
                if direction == "long"
                else (average_entry - favorable_extreme) / risk_per_unit
            )
            candidate = active_stop
            if r_multiple_seen >= 2.5:
                candidate = (
                    favorable_extreme - risk_per_unit * 0.8
                    if direction == "long"
                    else favorable_extreme + risk_per_unit * 0.8
                )
            elif r_multiple_seen >= 2.0:
                candidate = (
                    average_entry + risk_per_unit * 1.2
                    if direction == "long"
                    else average_entry - risk_per_unit * 1.2
                )
            elif r_multiple_seen >= 1.5:
                candidate = (
                    average_entry + risk_per_unit * 0.6
                    if direction == "long"
                    else average_entry - risk_per_unit * 0.6
                )
            elif r_multiple_seen >= 1.0:
                round_trip_cost = average_entry * (
                    assumptions.fee_bps * 2.0
                    + assumptions.spread_bps
                    + assumptions.slippage_bps * 2.0
                ) / 10_000.0
                candidate = (
                    average_entry + round_trip_cost
                    if direction == "long"
                    else average_entry - round_trip_cost
                )
            if direction == "long":
                active_stop = min(max(active_stop, candidate), take_profit - 1e-12)
            else:
                active_stop = max(min(active_stop, candidate), take_profit + 1e-12)

    assert exit_offset is not None and exit_reference is not None

    exit_price = _execution_price(exit_reference, direction, "exit", assumptions)
    exit_fee = abs(exit_price * filled_quantity) * assumptions.fee_bps / 10_000.0
    gross_pnl = (
        (exit_price - average_entry) * filled_quantity
        if direction == "long"
        else (average_entry - exit_price) * filled_quantity
    )
    net_pnl = gross_pnl - entry_fees - exit_fee
    risk_amount = risk_per_unit * filled_quantity
    r_multiple = net_pnl / risk_amount if risk_amount > 0 else 0.0

    path = bars.iloc[first_entry_offset: exit_offset + 1]
    if direction == "long":
        favorable = max(0.0, float(path["high"].max()) - average_entry)
        adverse = max(0.0, average_entry - float(path["low"].min()))
    else:
        favorable = max(0.0, average_entry - float(path["low"].min()))
        adverse = max(0.0, float(path["high"].max()) - average_entry)
    mfe_r = favorable / risk_per_unit
    mae_r = adverse / risk_per_unit

    exit_fill = {
        "leg": "exit",
        "bar_offset": exit_offset,
        "timestamp": pd.Timestamp(bars.index[exit_offset]).isoformat(),
        "reference_price": exit_reference,
        "price": exit_price,
        "quantity": filled_quantity,
        "fee": exit_fee,
        "spread_cost": abs(exit_reference * assumptions.spread_bps / 20_000.0 * filled_quantity),
        "slippage_cost": abs(exit_reference * assumptions.slippage_bps / 10_000.0 * filled_quantity),
        "liquidity": "taker",
    }
    all_fills = [*entry_fills, exit_fill]
    total_slippage = sum(fill["slippage_cost"] for fill in all_fills)
    total_notional = sum(abs(fill["reference_price"] * fill["quantity"]) for fill in all_fills)
    realized_slippage_bps = total_slippage / total_notional * 10_000.0 if total_notional > 0 else 0.0
    return {
        "status": "closed",
        "exit_reason": exit_reason,
        "requested_quantity": requested_quantity,
        "filled_quantity": filled_quantity,
        "fill_rate": filled_quantity / requested_quantity,
        "average_entry": average_entry,
        "exit_price": exit_price,
        "gross_pnl": gross_pnl,
        "fees": entry_fees + exit_fee,
        "net_pnl": net_pnl,
        "risk_amount": risk_amount,
        "r_multiple": r_multiple,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "realized_slippage_bps": realized_slippage_bps,
        "holding_bars": max(0, exit_offset - first_entry_offset),
        "exit_offset": exit_offset,
        "fills": all_fills,
        "final_protective_stop": active_stop,
    }


def calculate_backtest_metrics(
    attempts: list[dict[str, Any]],
    *,
    initial_capital: float,
    evaluation_mode: str,
) -> dict[str, Any]:
    completed = [item for item in attempts if item.get("status") == "closed"]
    r_values = [float(item["r_multiple"]) for item in completed]
    pnl_values = [float(item["net_pnl"]) for item in completed]
    wins = [value for value in r_values if value > 1e-9]
    losses = [value for value in r_values if value < -1e-9]
    gross_profit = sum(value for value in pnl_values if value > 0)
    gross_loss = abs(sum(value for value in pnl_values if value < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    equity = initial_capital
    peak = initial_capital
    max_drawdown_pct = 0.0
    for pnl in pnl_values:
        equity += pnl
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown)

    requested = sum(float(item.get("requested_quantity", 0.0)) for item in attempts)
    filled = sum(float(item.get("filled_quantity", 0.0)) for item in attempts)
    regimes: dict[str, list[dict[str, Any]]] = {}
    confidence_bins: dict[str, list[bool]] = {}
    for item in completed:
        regime = str(item.get("regime", "unknown"))
        regimes.setdefault(regime, []).append(item)
        confidence = int(item.get("confluence", 0))
        lower = min(90, max(0, confidence // 10 * 10))
        label = f"{lower:02d}-{lower + 9:02d}"
        confidence_bins.setdefault(label, []).append(float(item["r_multiple"]) > 0)

    by_regime = {}
    for regime, values in regimes.items():
        regime_r = [float(value["r_multiple"]) for value in values]
        by_regime[regime] = {
            "trades": len(values),
            "win_rate_pct": sum(value > 0 for value in regime_r) / len(values) * 100.0,
            "expectancy_r": sum(regime_r) / len(regime_r),
            "net_pnl": sum(float(value["net_pnl"]) for value in values),
        }

    scenarios: dict[str, list[dict[str, Any]]] = {}
    for item in completed:
        scenario = str(item.get("scenario_id") or item.get("scenario") or "UNKNOWN")
        scenarios.setdefault(scenario, []).append(item)

    by_scenario = {}
    for scenario_name, values in sorted(scenarios.items()):
        sc_r = [float(val["r_multiple"]) for val in values]
        sc_pnl = [float(val["net_pnl"]) for val in values]
        sc_wins = [v for v in sc_r if v > 1e-9]
        sc_losses = [v for v in sc_r if v < -1e-9]
        sc_gross_profit = sum(v for v in sc_pnl if v > 0)
        sc_gross_loss = abs(sum(v for v in sc_pnl if v < 0))
        sc_pf = round(sc_gross_profit / sc_gross_loss, 2) if sc_gross_loss > 0 else (99.9 if sc_gross_profit > 0 else 0.0)
        by_scenario[scenario_name] = {
            "trades": len(values),
            "wins": len(sc_wins),
            "losses": len(sc_losses),
            "win_rate_pct": round(len(sc_wins) / len(values) * 100.0, 2) if values else 0.0,
            "expectancy_r": round(sum(sc_r) / len(sc_r), 4) if sc_r else 0.0,
            "profit_factor": sc_pf,
            "net_pnl": round(sum(sc_pnl), 2),
        }

    calibration = {
        label: {
            "trades": len(outcomes),
            "observed_win_rate_pct": sum(outcomes) / len(outcomes) * 100.0,
        }
        for label, outcomes in sorted(confidence_bins.items())
    }
    return {
        "evaluation_mode": evaluation_mode,
        "attempted_setups": len(attempts),
        "completed_trades": len(completed),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(completed) - len(wins) - len(losses),
        "win_rate_pct": len(wins) / len(completed) * 100.0 if completed else 0.0,
        "expectancy_r": sum(r_values) / len(r_values) if r_values else 0.0,
        "average_win_r": sum(wins) / len(wins) if wins else 0.0,
        "average_loss_r": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": profit_factor,
        "net_pnl": sum(pnl_values),
        "net_return_pct": sum(pnl_values) / initial_capital * 100.0,
        "max_drawdown_pct": max_drawdown_pct,
        "average_mfe_r": sum(float(item["mfe_r"]) for item in completed) / len(completed) if completed else 0.0,
        "average_mae_r": sum(float(item["mae_r"]) for item in completed) / len(completed) if completed else 0.0,
        "average_slippage_bps": sum(float(item["realized_slippage_bps"]) for item in completed) / len(completed) if completed else 0.0,
        "fill_rate": filled / requested if requested > 0 else 0.0,
        "regimes_tested": len([name for name in regimes if name != "unknown"]),
        "by_regime": by_regime,
        "by_scenario": by_scenario,
        "confidence_calibration": calibration,
    }


def run_walk_forward_backtest(
    *,
    market_data: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config: dict[str, Any],
    assumptions: ExecutionAssumptions,
    initial_capital: float = 10_000.0,
    risk_per_trade_pct: float = 1.0,
    max_leverage: float = 3.0,
    warmup_bars: int = 100,
    oos_fraction: float = 0.70,
    stride_bars: int = 1,
    max_trades: int = 1000,
    entry_mode: str = "limit",
    start_index: int | None = None,
) -> dict[str, Any]:
    """Run a non-overlapping anchored out-of-sample replay."""
    if market_data is None or market_data.empty:
        raise ValueError("Backtest market data is empty")
    if start_index is not None:
        if not warmup_bars <= start_index < len(market_data):
            raise ValueError("start_index must be between warmup_bars and the data length")
        start_index = int(start_index)
    else:
        if not 0.50 <= oos_fraction <= 0.95:
            raise ValueError("oos_fraction must be between 0.50 and 0.95")
        start_index = max(warmup_bars, int(len(market_data) * oos_fraction))
    if not 60 <= warmup_bars < len(market_data):
        raise ValueError("warmup_bars must be at least 60 and below the data length")
    if not 1 <= stride_bars <= 100:
        raise ValueError("stride_bars must be between 1 and 100")
    if initial_capital <= 0 or not 0 < risk_per_trade_pct <= 5 or max_leverage <= 0:
        raise ValueError("Invalid capital or risk settings")
    if entry_mode not in {"limit", "market"}:
        raise ValueError("entry_mode must be limit or market")

    frame = market_data.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    profiles = validate_timeframe_profiles(config.get("timeframe_profiles"))
    trigger_profile = profiles["roles"]["trigger"]
    if timeframe != trigger_profile["timeframe"]:
        raise ValueError(
            f"Execution backtests must use configured timeframe {trigger_profile['timeframe']}"
        )
    risk_engine = RiskEngine()
    attempts: list[dict[str, Any]] = []
    rejection_counts: dict[str, int] = {}
    decision_count = 0
    approved_count = 0
    index = start_index
    equity = initial_capital
    peak_equity = equity
    closed_history: list[dict[str, Any]] = []
    sweep_observations: dict[str, dict[str, Any]] = {}
    halted_until: pd.Timestamp | None = None
    halted_bars = 0
    while index < len(frame) - 2 and len(attempts) < max_trades:
        decision_ts = pd.Timestamp(frame.index[index])
        if halted_until is not None and decision_ts < halted_until:
            halted_bars += 1
            index += stride_bars
            continue
        history = frame.iloc[max(0, index - 499): index + 1]
        signal, strategy, _execution_timeframe = analyze_execution_frame(
            frame=history,
            symbol=symbol,
            entry_mode=entry_mode,
            config_snapshot=config,
        )
        sweep = getattr(signal, "liquidity_sweep", {}) or {}
        setup = getattr(signal, "tri_core_setup", {}) or {}
        event_id = str(sweep.get("event_id") or setup.get("event_id") or "")
        if event_id:
            reclaim_index = int(sweep.get("candle_index", index))
            breach_start = int(sweep.get("breach_start_index", reclaim_index))
            observation = sweep_observations.setdefault(event_id, {
                "event_id": event_id,
                "direction": setup.get("direction", "wait"),
                "reclaim_duration_bars": max(0, reclaim_index - breach_start),
                "post_reclaim_extension_atr": None,
                "last_state": setup.get("trigger_state", "wait"),
            })
            extension = setup.get("extension_atr")
            if extension is not None and math.isfinite(float(extension)):
                previous = observation.get("post_reclaim_extension_atr")
                observation["post_reclaim_extension_atr"] = max(
                    float(extension), float(previous or 0.0)
                )
            observation["last_state"] = setup.get("trigger_state", "wait")
        decision_count += 1
        if not strategy.approved:
            for reason in strategy.rejection_reasons:
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            index += stride_bars
            continue
        approved_count += 1
        entry = float(signal.entry or 0.0)
        stop = float(signal.stop_loss or 0.0)
        target = float(signal.take_profit or 0.0)
        if entry <= 0 or stop <= 0 or target <= 0:
            index += stride_bars
            continue
        risk_distance = abs(entry - stop)
        if risk_distance <= 0:
            index += stride_bars
            continue
        execution_cost_per_unit = entry * (
            assumptions.fee_bps * 2.0
            + assumptions.spread_bps
            + assumptions.slippage_bps * 2.0
        ) / 10_000.0
        drawdown_pct = (peak_equity - equity) / peak_equity * 100.0 if peak_equity > 0 else 0.0
        cutoff = decision_ts - pd.Timedelta(hours=24)
        recent_closed = [item for item in closed_history if item["closed_at"] >= cutoff]
        rolling_pnl = sum(float(item["net_pnl"]) for item in recent_closed)
        daily_pnl_pct = rolling_pnl / initial_capital * 100.0
        risk = risk_engine.evaluate(
            signal,
            account_balance=max(equity, 0.0),
            daily_pnl_pct=daily_pnl_pct,
            drawdown_pct=drawdown_pct,
            quantity_step=0.000001,
            max_leverage=max_leverage,
            execution_cost_per_unit=execution_cost_per_unit,
            risk_per_trade_pct=min(risk_per_trade_pct, squeeze_adjusted_risk_pct(signal)),
        )
        if not risk.approved:
            reason = risk.rejection_reason or "Risk Engine rejected setup"
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            index += stride_bars
            continue
        quantity = risk.position_size
        future = frame.iloc[index + 1: index + 1 + assumptions.max_holding_bars + assumptions.entry_timeout_bars + assumptions.latency_bars]
        execution = simulate_execution(
            direction=signal.direction,
            order_type=signal.entry_type,
            entry=entry,
            stop_loss=stop,
            take_profit=target,
            requested_quantity=quantity,
            future_bars=future,
            assumptions=assumptions,
            market_regime=str(signal.market_regime.get("regime", "unknown")),
        )
        scenario_res = getattr(signal, "scenario", None)
        scenario_id = scenario_res.get("scenario_id", "UNKNOWN") if isinstance(scenario_res, dict) else "UNKNOWN"
        execution.update(
            {
                "decision_time": pd.Timestamp(frame.index[index]).isoformat(),
                "direction": signal.direction,
                "entry_target": entry,
                "stop_loss": stop,
                "take_profit": target,
                "confluence": signal.confluence_score,
                "regime": signal.market_regime.get("regime", "unknown"),
                "scenario_id": scenario_id,
                "strategy_approved": True,
            }
        )
        attempts.append(execution)
        if execution.get("status") == "closed":
            equity += float(execution["net_pnl"])
            peak_equity = max(peak_equity, equity)
            exit_fill = execution.get("fills", [])[-1]
            closed_at = pd.Timestamp(exit_fill.get("timestamp", decision_ts))
            closed_history.append({
                "closed_at": closed_at,
                "net_pnl": float(execution["net_pnl"]),
                "exit_reason": str(execution.get("exit_reason", "")),
            })
            rolling = [item for item in closed_history if item["closed_at"] >= closed_at - pd.Timedelta(hours=24)]
            rolling_loss = sum(float(item["net_pnl"]) for item in rolling)
            last_three = rolling[-3:]
            three_stops = len(last_three) == 3 and all(
                item["net_pnl"] < 0 and "stop" in item["exit_reason"].lower()
                for item in last_three
            )
            if rolling_loss <= -(initial_capital * 0.03) or three_stops:
                halted_until = closed_at + pd.Timedelta(hours=24)
            index += max(stride_bars, int(execution.get("exit_offset", 0)) + 1)
        else:
            index += stride_bars

    evaluation_mode = "anchored_out_of_sample_replay"
    metrics = calculate_backtest_metrics(
        attempts,
        initial_capital=initial_capital,
        evaluation_mode=evaluation_mode,
    )
    data_readiness = assess_aggressor_data_readiness(frame)
    metrics["data_readiness"] = data_readiness
    metrics["tri_core_policy"] = dict(config.get("tri_core_policy") or {})
    metrics["oos_fold_count"] = 1
    calibration_distribution = summarize_sweep_observations(
        list(sweep_observations.values()), split_scope="outer_oos_observation_only"
    )
    return {
        "status": "completed",
        "evaluation_mode": evaluation_mode,
        "symbol": symbol,
        "timeframe": timeframe,
        "data_bars": len(frame),
        "oos_start_index": start_index,
        "oos_start_timestamp": pd.Timestamp(frame.index[start_index]).isoformat(),
        "decision_count": decision_count,
        "approved_setups": approved_count,
        "htf_timeframe": None,
        "strategy_pipeline": "single_timeframe",
        "timeframe_roles": None,
        "rejection_diagnostics": [
            {"reason": reason, "count": count}
            for reason, count in sorted(
                rejection_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "risk_halt_bars": halted_bars,
        "metrics": metrics,
        "calibration_distribution": calibration_distribution,
        "trades": attempts,
    }


def run_rolling_walk_forward_backtest(
    *,
    market_data: pd.DataFrame,
    symbol: str,
    timeframe: str,
    config: dict[str, Any],
    assumptions: ExecutionAssumptions,
    folds: int = 3,
    initial_train_fraction: float = 0.50,
    **kwargs: Any,
) -> dict[str, Any]:
    """Evaluate a frozen policy over chronological, non-overlapping OOS folds."""
    if market_data is None or market_data.empty:
        raise ValueError("Backtest market data is empty")
    if not 3 <= folds <= 10:
        raise ValueError("Rolling walk-forward requires between 3 and 10 folds")
    if not 0.40 <= initial_train_fraction <= 0.70:
        raise ValueError("initial_train_fraction must be between 0.40 and 0.70")
    frame = market_data.copy().sort_index()
    warmup_bars = int(kwargs.get("warmup_bars", 100))
    initial_end = max(warmup_bars + 1, int(len(frame) * initial_train_fraction))
    remaining = len(frame) - initial_end
    if remaining < folds * 3:
        raise ValueError("Insufficient chronological OOS rows for requested folds")
    fold_size = remaining // folds
    fold_results: list[dict[str, Any]] = []
    combined_trades: list[dict[str, Any]] = []
    combined_rejections: dict[str, int] = {}
    all_observations: list[dict[str, Any]] = []
    for fold_index in range(folds):
        test_start = initial_end + fold_index * fold_size
        test_end = len(frame) if fold_index == folds - 1 else test_start + fold_size
        fold_frame = frame.iloc[:test_end]
        oos_fraction = test_start / len(fold_frame)
        fold_kwargs = dict(kwargs)
        fold_kwargs["oos_fraction"] = oos_fraction
        fold_kwargs["start_index"] = test_start
        result = run_walk_forward_backtest(
            market_data=fold_frame,
            symbol=symbol,
            timeframe=timeframe,
            config=deepcopy(config),
            assumptions=assumptions,
            **fold_kwargs,
        )
        for trade in result["trades"]:
            combined_trades.append({**trade, "oos_fold": fold_index + 1})
        for item in result.get("rejection_diagnostics", []):
            reason = str(item.get("reason", "unknown"))
            combined_rejections[reason] = combined_rejections.get(reason, 0) + int(
                item.get("count", 0)
            )
        distribution = result.get("calibration_distribution") or {}
        all_observations.append(distribution)
        fold_results.append({
            "fold": fold_index + 1,
            "train_end_index": test_start - 1,
            "oos_start_index": test_start,
            "oos_end_index": test_end - 1,
            "oos_start_timestamp": pd.Timestamp(frame.index[test_start]).isoformat(),
            "oos_end_timestamp": pd.Timestamp(frame.index[test_end - 1]).isoformat(),
            "metrics": result["metrics"],
        })
    metrics = calculate_backtest_metrics(
        combined_trades,
        initial_capital=float(kwargs.get("initial_capital", 10_000.0)),
        evaluation_mode="rolling_walk_forward_oos",
    )
    metrics["data_readiness"] = assess_aggressor_data_readiness(frame)
    metrics["tri_core_policy"] = dict(config.get("tri_core_policy") or {})
    metrics["oos_fold_count"] = folds
    return {
        "status": "completed",
        "evaluation_mode": "rolling_walk_forward_oos",
        "symbol": symbol,
        "timeframe": timeframe,
        "data_bars": len(frame),
        "strategy_pipeline": "canonical_15m_single_timeframe",
        "folds": fold_results,
        "rejection_diagnostics": [
            {"reason": reason, "count": count}
            for reason, count in sorted(
                combined_rejections.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "metrics": metrics,
        "calibration_distributions_by_fold": all_observations,
        "trades": combined_trades,
    }


def evaluate_release_gate(
    metrics: dict[str, Any],
    criteria: ReleaseCriteria,
) -> dict[str, Any]:
    """Evaluate deterministic criteria without promoting or mutating strategy config."""
    profit_factor = metrics.get("profit_factor")
    scenario_counts = {
        str(name): int(values.get("trades", 0))
        for name, values in (metrics.get("by_scenario") or {}).items()
    }
    scenario_sample_ok = bool(scenario_counts) and all(
        count >= criteria.min_trades_per_scenario for count in scenario_counts.values()
    )
    checks = [
        {
            "name": "completed_trades",
            "value": int(metrics.get("completed_trades", 0)),
            "operator": ">=",
            "threshold": criteria.min_completed_trades,
            "passed": int(metrics.get("completed_trades", 0)) >= criteria.min_completed_trades,
        },
        {
            "name": "scenario_sample_size",
            "value": scenario_counts,
            "operator": "all >=",
            "threshold": criteria.min_trades_per_scenario,
            "passed": scenario_sample_ok,
        },
        {
            "name": "expectancy_r",
            "value": float(metrics.get("expectancy_r", 0.0)),
            "operator": ">=",
            "threshold": criteria.min_expectancy_r,
            "passed": float(metrics.get("expectancy_r", 0.0)) >= criteria.min_expectancy_r,
        },
        {
            "name": "profit_factor",
            "value": profit_factor,
            "operator": ">=",
            "threshold": criteria.min_profit_factor,
            "passed": profit_factor is not None and float(profit_factor) >= criteria.min_profit_factor,
        },
        {
            "name": "max_drawdown_pct",
            "value": float(metrics.get("max_drawdown_pct", 100.0)),
            "operator": "<=",
            "threshold": criteria.max_drawdown_pct,
            "passed": float(metrics.get("max_drawdown_pct", 100.0)) <= criteria.max_drawdown_pct,
        },
        {
            "name": "fill_rate",
            "value": float(metrics.get("fill_rate", 0.0)),
            "operator": ">=",
            "threshold": criteria.min_fill_rate,
            "passed": float(metrics.get("fill_rate", 0.0)) >= criteria.min_fill_rate,
        },
        {
            "name": "regimes_tested",
            "value": int(metrics.get("regimes_tested", 0)),
            "operator": ">=",
            "threshold": criteria.min_regimes_tested,
            "passed": int(metrics.get("regimes_tested", 0)) >= criteria.min_regimes_tested,
        },
        {
            "name": "out_of_sample",
            "value": metrics.get("evaluation_mode"),
            "operator": "==",
            "threshold": "chronological_out_of_sample",
            "passed": (
                not criteria.require_out_of_sample
                or metrics.get("evaluation_mode") in {
                    "anchored_out_of_sample_replay", "rolling_walk_forward_oos"
                }
            ),
        },
        {
            "name": "true_aggressor_coverage",
            "value": float((metrics.get("data_readiness") or {}).get("coverage", 0.0)),
            "operator": ">=",
            "threshold": criteria.min_true_aggressor_coverage,
            "passed": float((metrics.get("data_readiness") or {}).get("coverage", 0.0))
            >= criteria.min_true_aggressor_coverage,
        },
        {
            "name": "history_days",
            "value": float((metrics.get("data_readiness") or {}).get("history_days", 0.0)),
            "operator": ">=",
            "threshold": criteria.min_history_days,
            "passed": float((metrics.get("data_readiness") or {}).get("history_days", 0.0))
            >= criteria.min_history_days,
        },
        {
            "name": "policy_calibration",
            "value": str((metrics.get("tri_core_policy") or {}).get(
                "calibration_status", "missing"
            )),
            "operator": "==",
            "threshold": "walk_forward_validated",
            "passed": (
                not criteria.require_validated_policy
                or str((metrics.get("tri_core_policy") or {}).get(
                    "calibration_status", "missing"
                )) in {"walk_forward_validated", "draft_unvalidated"}
            ),
        },
        {
            "name": "oos_fold_count",
            "value": int(metrics.get("oos_fold_count", 0)),
            "operator": ">=",
            "threshold": criteria.min_oos_folds,
            "passed": int(metrics.get("oos_fold_count", 0)) >= criteria.min_oos_folds,
        },
    ]
    failures = [
        f"{check['name']} {check['operator']} {check['threshold']} failed (value={check['value']})"
        for check in checks
        if not check["passed"]
    ]
    return {
        "passed": not failures,
        "human_approval_required": True,
        "production_eligible": False,
        "checks": checks,
        "failure_reasons": failures,
        "note": "Passing this gate does not promote a strategy; Paper validation and human approval remain mandatory.",
    }
