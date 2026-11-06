import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from app.engines.smc_engine import SMCSignal
from app.services.event_trigger import MarketMonitor


def _create_sample_signal(symbol="TEST/USDT", direction="long"):
    return SMCSignal(
        symbol=symbol,
        timeframe="15m",
        direction=direction,
        entry=101.0,
        stop_loss=98.0,
        take_profit=107.0,
        risk_reward=2.0,
        current_price=101.0,
        htf_bias="neutral",
        market_regime={"regime": "ranging"},
        tri_core_setup={
            "actionable": True,
            "direction": direction,
            "grade": "S",
            "setup_type": "sweep_reversal",
            "risk_reward": 2.0,
            "entry_policy": "market_eligible",
            "event_id": f"sweep:{symbol}:15m:1",
        },
    )


def _create_sample_df():
    times = pd.date_range("2026-11-02 12:00:00", periods=20, freq="15min", tz="UTC")
    return pd.DataFrame({
        "open": [100.0] * 20,
        "high": [105.0] * 20,
        "low": [95.0] * 20,
        "close": [101.0] * 20,
        "volume": [1000.0] * 20,
    }, index=times)


@pytest.mark.anyio
async def test_auto_pilot_e2e_order_payload_and_submission():
    """
    Anti-Regression Test:
    Ensures _evaluate_and_execute_auto_pilot successfully constructs order_payload
    and reaches paper_oms.place_order without NameError (such as undefined ltf_df).
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal()

    strat = MagicMock()
    strat.approved = True
    strat.direction = "long"

    mock_oms = MagicMock()
    mock_oms.ready = True
    mock_oms.list_positions = AsyncMock(return_value=[])
    mock_oms.account_snapshot = AsyncMock(return_value={
        "total_equity": 1000.0,
        "initial_capital": 1000.0,
        "daily_pnl_pct": 0.0,
    })
    mock_oms.place_order = AsyncMock(return_value={"status": "filled", "order_id": "order-123"})

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 4)
    rule_mock.min_quantity = 0.01
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.01

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={"auto_trade_enabled": True, "risk_per_trade": 2.0}), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", AsyncMock()), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)
        mock_lifecycle.transition = AsyncMock(return_value=True)

        res = await monitor._evaluate_and_execute_auto_pilot(
            symbol="TEST/USDT",
            direction="long",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=101.0,
            strat_res=strat,
            confluence=85,
            entry_mode="market",
            decision_snapshot_id="test-snap-001",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert mock_oms.place_order.called, "paper_oms.place_order MUST be called when auto_trade_enabled is True"
        payload = mock_oms.place_order.call_args[0][0]
        assert payload["symbol"] == "TEST/USDT"
        assert payload["direction"] == "long"
        assert payload["signal_bar_time"] == "2026-11-02 16:45:00+00:00"


@pytest.mark.anyio
async def test_auto_pilot_cluster_loss_cooldown_blocks_reentry():
    """
    Cluster Loss Cooldown Test:
    Ensures that when a symbol recently closed with a loss within cooldown_minutes,
    re-entry on the same symbol is rejected with CLUSTER_LOSS_COOLDOWN and place_order is NOT called.
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("DOT/USDT", "long")

    strat = MagicMock()
    strat.approved = True
    strat.direction = "long"

    # Closed with loss 5 minutes ago
    loss_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    mock_positions = [
        {
            "symbol": "DOT/USDT",
            "status": "closed",
            "realized_pnl_net": -14.50,
            "close_reason": "Stop Loss (SL Hit) 🛑",
            "closed_at": loss_time,
        }
    ]

    mock_oms = MagicMock()
    mock_oms.ready = True
    mock_oms.list_positions = AsyncMock(return_value={"trades": mock_positions})
    mock_oms.account_snapshot = AsyncMock(return_value={
        "total_equity": 1000.0,
        "initial_capital": 1000.0,
        "daily_pnl_pct": 0.0,
    })
    mock_oms.place_order = AsyncMock()

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 4)
    rule_mock.min_quantity = 0.01
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.01

    mock_trace = AsyncMock()

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "cluster_loss_cooldown_enabled": True,
             "cluster_loss_cooldown_minutes": 30,
         }), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", mock_trace), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)

        res = await monitor._evaluate_and_execute_auto_pilot(
            symbol="DOT/USDT",
            direction="long",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=101.0,
            strat_res=strat,
            confluence=85,
            entry_mode="market",
            decision_snapshot_id="test-snap-cooldown",
            setup_timeframe="15m",
            ltf_df=df,
        )

        # Order must be blocked
        assert res is None, "Should return None when cluster cooldown is active"
        assert not mock_oms.place_order.called, "place_order MUST NOT be called within cooldown window"
        
        # Verify trace recorded reason_code == CLUSTER_LOSS_COOLDOWN
        cooldown_traces = [call for call in mock_trace.call_args_list if call.kwargs.get("facts", {}).get("reason_code") == "CLUSTER_LOSS_COOLDOWN"]
        assert len(cooldown_traces) > 0, "CLUSTER_LOSS_COOLDOWN reason must be recorded in decision trace"


@pytest.mark.anyio
async def test_auto_pilot_cluster_loss_cooldown_allows_after_expiry():
    """
    Cluster Loss Cooldown Expiry Test:
    Ensures that once cooldown_minutes has passed (e.g. 35 mins ago),
    new signals for the symbol are allowed to execute cleanly.
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("DOT/USDT", "long")

    strat = MagicMock()
    strat.approved = True
    strat.direction = "long"

    # Closed with loss 35 minutes ago (> 30 min cooldown)
    loss_time = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
    mock_positions = [
        {
            "symbol": "DOT/USDT",
            "status": "closed",
            "realized_pnl_net": -14.50,
            "close_reason": "Stop Loss (SL Hit) 🛑",
            "closed_at": loss_time,
        }
    ]

    mock_oms = MagicMock()
    mock_oms.ready = True
    mock_oms.list_positions = AsyncMock(return_value={"trades": mock_positions})
    mock_oms.account_snapshot = AsyncMock(return_value={
        "total_equity": 1000.0,
        "initial_capital": 1000.0,
        "daily_pnl_pct": 0.0,
    })
    mock_oms.place_order = AsyncMock(return_value={"status": "filled", "order_id": "order-456"})

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 4)
    rule_mock.min_quantity = 0.01
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.01

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "cluster_loss_cooldown_enabled": True,
             "cluster_loss_cooldown_minutes": 30,
             "risk_per_trade": 2.0,
         }), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", AsyncMock()), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)
        mock_lifecycle.transition = AsyncMock(return_value=True)

        res = await monitor._evaluate_and_execute_auto_pilot(
            symbol="DOT/USDT",
            direction="long",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=101.0,
            strat_res=strat,
            confluence=85,
            entry_mode="market",
            decision_snapshot_id="test-snap-expired",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert mock_oms.place_order.called, "place_order MUST be called when cooldown has expired"


@pytest.mark.anyio
async def test_auto_pilot_loss_streak_halt_blocks_portfolio():
    """
    Loss Streak Circuit Breaker Test:
    Ensures that when 3 consecutive losses occurred across the portfolio within halt_hours,
    trading is halted and place_order is NOT called.
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("ETH/USDT", "long")

    strat = MagicMock()
    strat.approved = True
    strat.direction = "long"

    # 3 consecutive losses across portfolio 20 minutes ago
    t_20m = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    t_30m = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    t_40m = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()
    mock_positions = [
        {"symbol": "SOL/USDT", "status": "closed", "realized_pnl_net": -15.0, "close_reason": "Stop Loss", "closed_at": t_20m},
        {"symbol": "AVAX/USDT", "status": "closed", "realized_pnl_net": -12.0, "close_reason": "Stop Loss", "closed_at": t_30m},
        {"symbol": "ADA/USDT", "status": "closed", "realized_pnl_net": -10.0, "close_reason": "Stop Loss", "closed_at": t_40m},
    ]

    mock_oms = MagicMock()
    mock_oms.ready = True
    mock_oms.list_positions = AsyncMock(return_value={"trades": mock_positions})
    mock_oms.account_snapshot = AsyncMock(return_value={
        "total_equity": 1000.0,
        "initial_capital": 1000.0,
        "daily_pnl_pct": -3.7,
    })
    mock_oms.place_order = AsyncMock()

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 4)
    rule_mock.min_quantity = 0.01
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.01

    mock_trace = AsyncMock()

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "loss_streak_halt_enabled": True,
             "loss_streak_halt_hours": 2,
         }), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", mock_trace), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)

        res = await monitor._evaluate_and_execute_auto_pilot(
            symbol="ETH/USDT",
            direction="long",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=101.0,
            strat_res=strat,
            confluence=85,
            entry_mode="market",
            decision_snapshot_id="test-snap-streak",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert res is None
        assert not mock_oms.place_order.called
        streak_traces = [call for call in mock_trace.call_args_list if call.kwargs.get("facts", {}).get("reason_code") == "LOSS_STREAK_HALT"]
        assert len(streak_traces) > 0, "LOSS_STREAK_HALT reason must be recorded in decision trace"
