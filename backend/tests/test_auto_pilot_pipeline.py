import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.engines.smc_engine import SMCSignal
from app.services.event_trigger import MarketMonitor


@pytest.mark.anyio
async def test_auto_pilot_e2e_order_payload_and_submission():
    """
    Anti-Regression Test:
    Ensures _evaluate_and_execute_auto_pilot successfully constructs order_payload
    and reaches paper_oms.place_order without NameError (such as undefined ltf_df).
    """
    monitor = MarketMonitor(poll_interval_seconds=60)

    # 1. Create realistic 15M candle dataframe
    times = pd.date_range("2026-11-02 12:00:00", periods=20, freq="15min", tz="UTC")
    df = pd.DataFrame({
        "open": [100.0] * 20,
        "high": [105.0] * 20,
        "low": [95.0] * 20,
        "close": [101.0] * 20,
        "volume": [1000.0] * 20,
    }, index=times)

    # 2. Real SMCSignal dataclass (Grade S Actionable Setup)
    sig = SMCSignal(
        symbol="TEST/USDT",
        timeframe="15m",
        direction="long",
        entry=101.0,
        stop_loss=98.0,
        take_profit=107.0,
        risk_reward=2.0,
        current_price=101.0,
        htf_bias="neutral",
        market_regime={"regime": "ranging"},
        tri_core_setup={
            "actionable": True,
            "direction": "long",
            "grade": "S",
            "setup_type": "sweep_reversal",
            "risk_reward": 2.0,
            "entry_policy": "market_eligible",
            "event_id": "sweep:TEST/USDT:15m:1",
        },
    )

    strat = MagicMock()
    strat.approved = True
    strat.direction = "long"

    # 3. Patch dependencies
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

        # Run pipeline
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

        # 4. Verify order was submitted to paper_oms
        assert mock_oms.place_order.called, "paper_oms.place_order MUST be called when auto_trade_enabled is True"
        payload = mock_oms.place_order.call_args[0][0]
        assert payload["symbol"] == "TEST/USDT"
        assert payload["direction"] == "long"
        assert payload["signal_bar_time"] == "2026-11-02 16:45:00+00:00"
