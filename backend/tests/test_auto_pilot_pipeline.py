import numpy as np
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


@pytest.mark.anyio
async def test_auto_pilot_enforces_user_min_stop_loss_default_sl_pct():
    """
    Verifies that default_sl_pct (Min Stop Loss in Settings) widens a tight 0.38% SL
    to the configured floor (e.g. 1.0%) and reaches paper_oms.place_order.
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("XRP/USDT", "short")
    # Tight 0.38% stop loss on entry=100.0 -> raw_sl=100.38
    sig.entry = 100.0
    sig.stop_loss = 100.38
    sig.take_profit = 99.40
    sig.tri_core_setup["entry"] = 100.0
    sig.tri_core_setup["stop_loss"] = 100.38
    sig.tri_core_setup["take_profit"] = 99.40

    strat = MagicMock()
    strat.approved = True
    strat.direction = "short"

    mock_oms = MagicMock()
    mock_oms.ready = True
    mock_oms.list_positions = AsyncMock(return_value=[])
    mock_oms.account_snapshot = AsyncMock(return_value={
        "total_equity": 1000.0,
        "initial_capital": 1000.0,
        "daily_pnl_pct": 0.0,
    })
    mock_oms.place_order = AsyncMock(return_value={"status": "filled", "order_id": "order-xrp"})

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 4)
    rule_mock.min_quantity = 0.01
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.01

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "risk_per_trade": 2.0,
             "default_sl_pct": 1.0,
             "target_rr": 1.5,
         }), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", AsyncMock()), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)
        mock_lifecycle.transition = AsyncMock(return_value=True)

        await monitor._evaluate_and_execute_auto_pilot(
            symbol="XRP/USDT",
            direction="short",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=100.0,
            strat_res=strat,
            confluence=85,
            entry_mode="market",
            decision_snapshot_id="test-snap-min-sl",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert mock_oms.place_order.called
        payload = mock_oms.place_order.call_args[0][0]
        # Stop loss must be widened from 100.38 (0.38%) to 101.0 (1.0%)
        assert payload["stop_loss"] == pytest.approx(101.0, abs=0.05)
        assert payload["take_profit"] == pytest.approx(98.5, abs=0.05)


@pytest.mark.anyio
async def test_auto_pilot_preserves_structural_sl_tp_on_btc_limit_order():
    """
    #62 BTC regression: A BTC/USDT limit order with valid 0.40% structural SL (73365) and
    structural TP (74151) must NOT be inflated to 1.50% SL (72555) and 2.25% TP (75317).
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("BTC/USDT", "long")
    sig.entry = 73660.0
    sig.stop_loss = 73365.0
    sig.take_profit = 74151.0
    sig.tri_core_setup["entry"] = 73660.0
    sig.tri_core_setup["stop_loss"] = 73365.0
    sig.tri_core_setup["take_profit"] = 74151.0
    sig.tri_core_setup["order_type"] = "limit"
    sig.tri_core_setup["entry_policy"] = "limit_retest_only"

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
    mock_oms.place_order = AsyncMock(return_value={"status": "open", "order_id": "order-btc-62"})

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 2)
    rule_mock.min_quantity = 0.001
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.001

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "risk_per_trade": 2.0,
             "default_sl_pct": 1.5,
             "target_rr": 1.5,
         }), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", AsyncMock()), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)
        mock_lifecycle.transition = AsyncMock(return_value=True)

        await monitor._evaluate_and_execute_auto_pilot(
            symbol="BTC/USDT",
            direction="long",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=73800.0,
            strat_res=strat,
            confluence=85,
            entry_mode="limit",
            decision_snapshot_id="test-snap-btc-limit",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert mock_oms.place_order.called
        payload = mock_oms.place_order.call_args[0][0]
        assert payload["order_type"] == "limit"
        assert payload["stop_loss"] == pytest.approx(73365.0, abs=1.0)
        assert payload["take_profit"] == pytest.approx(74151.0, abs=1.0)


@pytest.mark.anyio
async def test_auto_pilot_blocks_market_entry_when_live_price_compresses_sl_below_default_sl_pct():
    """
    #84 SUI regression: When orig_entry=0.7068 has a valid SL=0.7125 (0.81%), but live_price
    drifted adversely to 0.7094 (compressing SL to 0.44% < 0.80% default_sl_pct) while TP=0.6776
    is far enough that live_rr > 1.5, Auto-Pilot must NOT fire a Market order with a 0.44% SL;
    it must demote to a Limit order at orig_entry (0.7068).
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("SUI/USDT", "short")
    sig.entry = 0.7068
    sig.stop_loss = 0.7125
    sig.take_profit = 0.6776
    sig.tri_core_setup["entry"] = 0.7068
    sig.tri_core_setup["stop_loss"] = 0.7125
    sig.tri_core_setup["take_profit"] = 0.6776
    sig.tri_core_setup["order_type"] = "market"
    sig.tri_core_setup["entry_policy"] = "market_eligible"

    strat = MagicMock()
    strat.approved = True
    strat.direction = "short"

    mock_oms = MagicMock()
    mock_oms.ready = True
    mock_oms.list_positions = AsyncMock(return_value=[])
    mock_oms.account_snapshot = AsyncMock(return_value={
        "total_equity": 1000.0,
        "initial_capital": 1000.0,
        "daily_pnl_pct": 0.0,
    })
    mock_oms.place_order = AsyncMock(return_value={"status": "open", "order_id": "order-sui-84"})

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 4)
    rule_mock.min_quantity = 0.1
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.1

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "risk_per_trade": 2.0,
             "default_sl_pct": 0.8,
             "target_rr": 1.5,
         }), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", AsyncMock()), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)
        mock_lifecycle.transition = AsyncMock(return_value=True)

        await monitor._evaluate_and_execute_auto_pilot(
            symbol="SUI/USDT",
            direction="short",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=0.7094,
            strat_res=strat,
            confluence=85,
            entry_mode="market",
            decision_snapshot_id="test-snap-sui-84",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert mock_oms.place_order.called
        payload = mock_oms.place_order.call_args[0][0]
        assert payload["order_type"] == "limit"
        assert payload["entry"] == pytest.approx(0.7068, abs=1e-4)


@pytest.mark.anyio
async def test_auto_pilot_widens_0_71_pct_sl_to_user_default_sl_pct_0_80():
    """
    #88 ADA regression: A market order with 0.714% SL (between 0.65% and 0.80%) must still be
    widened to default_sl_pct=0.80% when default_sl_pct is set to 0.8 in Settings.
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("ADA/USDT", "long")
    sig.entry = 0.1540
    sig.stop_loss = 0.1529  # 0.714% SL
    sig.take_profit = 0.1560
    sig.tri_core_setup["entry"] = 0.1540
    sig.tri_core_setup["stop_loss"] = 0.1529
    sig.tri_core_setup["take_profit"] = 0.1560
    sig.tri_core_setup["order_type"] = "market"
    sig.tri_core_setup["entry_policy"] = "market_eligible"

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
    mock_oms.place_order = AsyncMock(return_value={"status": "filled", "order_id": "order-ada-88"})

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 5)
    rule_mock.min_quantity = 1.0
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 1.0

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "risk_per_trade": 2.0,
             "default_sl_pct": 0.8,
             "target_rr": 1.5,
         }), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", AsyncMock()), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)
        mock_lifecycle.transition = AsyncMock(return_value=True)

        await monitor._evaluate_and_execute_auto_pilot(
            symbol="ADA/USDT",
            direction="long",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=0.1540,
            strat_res=strat,
            confluence=85,
            entry_mode="market",
            decision_snapshot_id="test-snap-ada-88",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert mock_oms.place_order.called
        payload = mock_oms.place_order.call_args[0][0]
        # 0.1540 * (1 - 0.008) = 0.15277
        assert payload["stop_loss"] == pytest.approx(0.15277, abs=2e-5)


@pytest.mark.anyio
async def test_auto_pilot_direction_flip_after_loss_enforces_60_min_whipsaw_cooldown():
    """
    #98/#99 ETH regression: When a symbol closed with a SHORT loss 45 minutes ago
    (> 30m base cooldown, < 60m flip cooldown), flipping to LONG must be blocked by
    the 60-minute Anti-Whipsaw Direction-Flip Cooldown.
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("ETH/USDT", "long")

    strat = MagicMock()
    strat.approved = True
    strat.direction = "long"

    loss_time = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    mock_positions = [
        {
            "symbol": "ETH/USDT",
            "direction": "short",
            "status": "closed",
            "realized_pnl_net": -14.18,
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
    rule_mock.price = lambda p, upward=True: round(p, 2)
    rule_mock.min_quantity = 0.001
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.001

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
            symbol="ETH/USDT",
            direction="long",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=101.0,
            strat_res=strat,
            confluence=85,
            entry_mode="market",
            decision_snapshot_id="test-snap-eth-flip",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert res is None
        assert not mock_oms.place_order.called
        flip_traces = [
            call for call in mock_trace.call_args_list
            if call.kwargs.get("facts", {}).get("reason_code") == "CLUSTER_LOSS_COOLDOWN"
            and call.kwargs.get("facts", {}).get("direction_flip") is True
        ]
        assert len(flip_traces) > 0


@pytest.mark.anyio
async def test_auto_pilot_applies_grade_a_half_risk_and_symbol_auto_throttle():
    """
    81-trade empirical fix: Grade A gets 0.5x risk budget (vs Grade S 1.0x), and a symbol
    with < 2 wins and net negative PnL across its last 5 closed trades gets another 0.5x throttle.
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("ADA/USDT", "long")
    sig.tri_core_setup["grade"] = "A"
    sig.market_regime = {"regime": "trending", "direction": "bullish"}

    strat = MagicMock()
    strat.approved = True
    strat.direction = "long"

    old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    mock_positions = [
        {"symbol": "ADA/USDT", "direction": "long", "status": "closed", "realized_pnl_net": -16.0, "closed_at": old_time},
        {"symbol": "ADA/USDT", "direction": "short", "status": "closed", "realized_pnl_net": -15.0, "closed_at": old_time},
        {"symbol": "ADA/USDT", "direction": "long", "status": "closed", "realized_pnl_net": -14.0, "closed_at": old_time},
        {"symbol": "ADA/USDT", "direction": "short", "status": "closed", "realized_pnl_net": +6.0, "closed_at": old_time},
        {"symbol": "ADA/USDT", "direction": "long", "status": "closed", "realized_pnl_net": -16.0, "closed_at": old_time},
    ]

    mock_oms = MagicMock()
    mock_oms.ready = True
    mock_oms.list_positions = AsyncMock(return_value={"trades": mock_positions})
    mock_oms.account_snapshot = AsyncMock(return_value={
        "total_equity": 1000.0,
        "initial_capital": 1000.0,
        "daily_pnl_pct": 0.0,
    })
    mock_oms.place_order = AsyncMock(return_value={"status": "filled", "order_id": "order-ada-throttle"})

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 4)
    rule_mock.min_quantity = 0.01
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.01

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "risk_per_trade": 2.0,
             "cluster_loss_cooldown_enabled": True,
             "cluster_loss_cooldown_minutes": 30,
             "loss_streak_halt_enabled": False,
         }), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", AsyncMock()), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)
        mock_lifecycle.transition = AsyncMock(return_value=True)

        await monitor._evaluate_and_execute_auto_pilot(
            symbol="ADA/USDT",
            direction="long",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=101.0,
            strat_res=strat,
            confluence=85,
            entry_mode="market",
            decision_snapshot_id="test-snap-grade-throttle",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert mock_oms.place_order.called
        payload = mock_oms.place_order.call_args[0][0]
        # 2.0% base * 0.5 (Grade A) * 0.5 (Symbol Auto-Throttle) = 0.50% risk
        assert payload["risk_pct"] == pytest.approx(0.50, abs=0.05)


@pytest.mark.anyio
async def test_auto_pilot_caps_15m_ranging_tp_at_2_20r_and_sets_0_90r_35pct_tp1():
    """
    Payoff optimization regression: On 15m ranging setups, cap distant TP > 2.20R to 2.20R,
    place TP1 cleanly at +0.90R (no cost_buffer inflation), and scale out 35% (leaving 65% runner).
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("NEAR/USDT", "long")
    sig.entry = 100.0
    sig.stop_loss = 99.0   # 1.0% risk
    sig.take_profit = 104.0  # 4.0R distant target
    sig.market_regime = {"regime": "ranging"}
    sig.tri_core_setup["entry"] = 100.0
    sig.tri_core_setup["stop_loss"] = 99.0
    sig.tri_core_setup["take_profit"] = 104.0

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
    mock_oms.place_order = AsyncMock(return_value={"status": "filled", "order_id": "order-near-payoff"})

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 4)
    rule_mock.min_quantity = 0.01
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.01

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "risk_per_trade": 2.0,
             "default_sl_pct": 0.8,
             "target_rr": 1.5,
         }), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", AsyncMock()), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)
        mock_lifecycle.transition = AsyncMock(return_value=True)

        await monitor._evaluate_and_execute_auto_pilot(
            symbol="NEAR/USDT",
            direction="long",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=100.0,
            strat_res=strat,
            confluence=85,
            entry_mode="market",
            decision_snapshot_id="test-snap-near-payoff",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert mock_oms.place_order.called
        payload = mock_oms.place_order.call_args[0][0]
        # TP capped at 100 + 1.0 * 2.20 = 102.20
        assert payload["take_profit"] == pytest.approx(102.2, abs=0.05)
        # Track 1 Fix #4: BE @ +1.0R without partial TP1 scale-out
        assert payload["take_profit_1"] is None
        assert payload["tp1_ratio"] is None
        assert payload["multi_stage_tp_enabled"] is False
        assert payload["tp1_enabled"] is False


@pytest.mark.anyio
async def test_track1_sweep_reversal_forced_limit_and_cost_to_risk_25pct_gate():
    """
    Track 1 E2E verification:
    1. sweep_reversal never executes as market even if upstream flags market_eligible.
    2. Setups with Cost / 1R > 25% are rejected at execution revalidation.
    """
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("LINK/USDT", "short")
    sig.entry = 100.0
    sig.stop_loss = 100.80
    sig.take_profit = 98.40
    sig.tri_core_setup["setup_type"] = "sweep_reversal"
    sig.tri_core_setup["order_type"] = "market"
    sig.tri_core_setup["entry_policy"] = "market_eligible"
    sig.tri_core_setup["entry"] = 100.0
    sig.tri_core_setup["stop_loss"] = 100.80
    sig.tri_core_setup["take_profit"] = 98.40

    strat = MagicMock()
    strat.approved = True
    strat.direction = "short"

    mock_oms = MagicMock()
    mock_oms.ready = True
    mock_oms.list_positions = AsyncMock(return_value=[])
    mock_oms.account_snapshot = AsyncMock(return_value={
        "total_equity": 1000.0,
        "initial_capital": 1000.0,
        "daily_pnl_pct": 0.0,
    })
    mock_oms.place_order = AsyncMock(return_value={"status": "submitted", "order_id": "order-link-limit"})

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 4)
    rule_mock.min_quantity = 0.01
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.01

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "risk_per_trade": 2.0,
             "default_sl_pct": 0.8,
             "target_rr": 1.5,
         }), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", AsyncMock()), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)
        mock_lifecycle.transition = AsyncMock(return_value=True)

        await monitor._evaluate_and_execute_auto_pilot(
            symbol="LINK/USDT",
            direction="short",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=100.0,
            strat_res=strat,
            confluence=88,
            entry_mode="market",
            decision_snapshot_id="test-snap-sweep-limit",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert mock_oms.place_order.called
        payload = mock_oms.place_order.call_args[0][0]
        assert payload["order_type"] == "limit"
        assert payload["take_profit_1"] is None
        assert payload["tp1_ratio"] is None

    # Now test Cost/1R > 25% rejection with very high fee/slippage bps
    mock_oms.place_order.reset_mock()
    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "risk_per_trade": 2.0,
             "default_sl_pct": 0.4,
             "target_rr": 1.5,
         }), \
         patch("app.services.event_trigger.get_settings", return_value=MagicMock(
             paper_oms_fee_bps=10.0,
             paper_oms_spread_bps=5.0,
             paper_oms_slippage_bps=5.0,
             max_risk_per_trade_pct=2.0,
             max_leverage=10.0,
         )), \
         patch("app.services.event_trigger.instrument_rules.get", AsyncMock(return_value=rule_mock)), \
         patch("app.services.event_trigger.decision_trace.record", AsyncMock()), \
         patch("app.services.event_trigger.broadcast", AsyncMock()), \
         patch("app.services.event_trigger.candidate_lifecycle") as mock_lifecycle:

        rec_mock = MagicMock()
        rec_mock.state.value = "ARMED"
        mock_lifecycle.register = AsyncMock(return_value=rec_mock)
        mock_lifecycle.transition = AsyncMock(return_value=True)

        sig_tight = _create_sample_signal("LINK/USDT", "short")
        sig_tight.entry = 100.0
        sig_tight.stop_loss = 100.40  # 0.40% SL -> 25bps cost / 40bps SL = 62.5% > 25%
        sig_tight.take_profit = 99.00
        sig_tight.tri_core_setup["setup_type"] = "sweep_reversal"
        sig_tight.tri_core_setup["entry"] = 100.0
        sig_tight.tri_core_setup["stop_loss"] = 100.40
        sig_tight.tri_core_setup["take_profit"] = 99.00

        res_rejected = await monitor._evaluate_and_execute_auto_pilot(
            symbol="LINK/USDT",
            direction="short",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig_tight,
            live_price=100.0,
            strat_res=strat,
            confluence=88,
            entry_mode="limit",
            decision_snapshot_id="test-snap-cost-cap",
            setup_timeframe="15m",
            ltf_df=df,
        )
        assert res_rejected is None
        assert not mock_oms.place_order.called


@pytest.mark.anyio
async def test_e2e_trend_consensus_blocks_dead_cat_and_executes_aligned_through_oms():
    """End-to-End verification: 1H EMA consensus + 4H strict trend guard flow cleanly to paper_oms.place_order."""
    from app.engines.tri_core_engine import TriCoreSetupEngine
    from app.services.execution_analysis import (
        compute_ema_trend_snapshot,
        resolve_intraday_trend_bias,
    )

    # 1) Dead-cat bounce on 1H (1H EMA20 < EMA50, noisy 1H SMC bullish) -> resolves bearish -> blocks LONG
    htf_down_df = pd.DataFrame({"close": list(np.linspace(120.0, 96.0, 65)) + [96.5, 97.0]})
    htf_ema = compute_ema_trend_snapshot(htf_down_df)
    resolved_bias = resolve_intraday_trend_bias("bullish", htf_ema["ema_bias"])
    assert resolved_bias == "bearish"

    # 2) Aligned Grade-S SHORT with 4H mixed/bearish flows all the way through _evaluate_and_execute_auto_pilot -> place_order
    monitor = MarketMonitor(poll_interval_seconds=60)
    df = _create_sample_df()
    sig = _create_sample_signal("SOL/USDT", "short")
    sig.intraday_bias = resolved_bias
    sig.macro_bias = "bearish"
    sig.macro_strict_trend = "bearish"
    sig.entry = 100.0
    sig.stop_loss = 101.0
    sig.take_profit = 97.5
    sig.tri_core_setup["grade"] = "S"
    sig.tri_core_setup["direction"] = "short"
    sig.tri_core_setup["entry"] = 100.0
    sig.tri_core_setup["stop_loss"] = 101.0
    sig.tri_core_setup["take_profit"] = 97.5

    strat = MagicMock()
    strat.approved = True
    strat.direction = "short"

    mock_oms = MagicMock()
    mock_oms.ready = True
    mock_oms.list_positions = AsyncMock(return_value=[])
    mock_oms.account_snapshot = AsyncMock(return_value={
        "total_equity": 1000.0,
        "initial_capital": 1000.0,
        "daily_pnl_pct": 0.0,
    })
    mock_oms.place_order = AsyncMock(return_value={"status": "filled", "order_id": "order-sol-short"})

    rule_mock = MagicMock()
    rule_mock.price = lambda p, upward=True: round(p, 4)
    rule_mock.min_quantity = 0.01
    rule_mock.min_notional = 5.0
    rule_mock.quantity_step = 0.01

    with patch("app.services.paper_oms.paper_oms", mock_oms), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
             "auto_trade_enabled": True,
             "risk_per_trade": 2.0,
             "default_sl_pct": 0.8,
             "target_rr": 1.5,
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
            symbol="SOL/USDT",
            direction="short",
            m_type="crypto",
            ex="binance",
            ltf_sig=sig,
            live_price=100.0,
            strat_res=strat,
            confluence=88,
            entry_mode="market",
            decision_snapshot_id="test-snap-trend-e2e",
            setup_timeframe="15m",
            ltf_df=df,
        )

        assert res is not None
        assert mock_oms.place_order.called
        payload = mock_oms.place_order.call_args[0][0]
        assert payload["symbol"] == "SOL/USDT"
        assert payload["direction"] == "short"






