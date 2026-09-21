"""Tests for Autonomous Auto-Pilot Execution in EventTriggerService."""

import pytest
from unittest.mock import AsyncMock, patch

from app.services.event_trigger import MarketMonitor
from app.engines.smc_engine import SMCSignal
from app.engines.strategy_engine import StrategyResult
from app.services.mtf_analysis import MTFAnalysis, TimeframeStage


@pytest.fixture(autouse=True)
def reset_auto_trade_history():
    from app.services.event_trigger import _AUTO_TRADE_HISTORY
    _AUTO_TRADE_HISTORY.clear()
    yield
    _AUTO_TRADE_HISTORY.clear()


@pytest.fixture
def mock_mtf():
    # 4H Bias Stage
    sig_4h = SMCSignal(symbol="MSFT", timeframe="4h", bias="bullish", confluence=80)
    stage_4h = TimeframeStage(role="bias", timeframe="4h", status="ready", direction="long", signal=sig_4h)

    # 1H Setup Stage
    sig_1h = SMCSignal(symbol="MSFT", timeframe="1h", bias="bullish", confluence=75)
    stage_1h = TimeframeStage(role="setup", timeframe="1h", status="ready", direction="long", signal=sig_1h)

    # 15M Trigger Stage (Grade S with Squeeze Fire)
    sig_15m = SMCSignal(
        symbol="MSFT",
        timeframe="15m",
        bias="bullish",
        confluence=85,
        squeeze_status="squeeze_fire",
        squeeze_momentum=1.5,
        momentum_direction="accelerating_up",
        current_price=500.0,
        stop_loss=496.0,
        take_profit=516.0,
        risk_reward=4.0,
        scenario={"scenario_id": "TEST_CONFIRMED", "actionable": True},
        volume_quality="exchange_aggressor",
        ai_review={
            "status": "reviewed",
            "verdict": "COHERENT",
            "execution_action": "ENTER_NOW",
            "scalper_bias": "BULLISH_SCALP",
            "reason": "Delta absorption confirmed at discount",
        },
        indicator_decision={"ready": True, "squeeze_bonus": 10, "squeeze_bonus_max": 10},
        tri_core_setup={"actionable": True, "direction": "long", "grade": "S",
                        "setup_type": "sweep_reversal",
                        "entry_policy": "market_eligible", "order_type": "market",
                        "event_id": "sweep:MSFT:15m:test",
                        "smc_confirmed": True, "cvd_confirmed": True,
                        "flow_confirmed": True},
    )
    stage_15m = TimeframeStage(role="trigger", timeframe="15m", status="ready", direction="long", signal=sig_15m)

    strat_res = StrategyResult(
        approved=True,
        direction="long",
        score=85,
        strategy_name="trending_bullish",
        effective_policy={"min_confluence": 65},
    )

    return MTFAnalysis(
        symbol="MSFT",
        status="ready",
        direction="long",
        actionable=True,
        stages={"bias": stage_4h, "setup": stage_1h, "trigger": stage_15m},
        strategy=strat_res,
        profile={},
    )


@pytest.mark.asyncio
async def test_auto_pilot_executes_grade_s(mock_mtf):
    service = MarketMonitor()

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_min_grade": "A",
        "auto_trade_entry_type": "momentum_market",
        "auto_trade_cooldown_seconds": 300,
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock(return_value={"id": "order-123", "status": "filled"})) as mock_create, \
       patch("app.services.event_trigger.broadcast", AsyncMock()), \
       patch.object(service.notifier, "send_signal_alert", AsyncMock()):

        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT",
            direction="long",
            m_type="stock",
            ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal,
            live_price=500.0,
            strat_res=mock_mtf.strategy,
            confluence=85,
            entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

        assert result is not None
        assert result["id"] == "order-123"
        assert mock_create.called
        call_args = mock_create.call_args[0][0]
        assert call_args["symbol"] == "MSFT"
        assert call_args["direction"] == "long"
        assert call_args["entry"] == 500.0
        assert call_args["auto_be"] is True
        assert call_args["trailing_stop"] is True
        assert call_args["setup_grade"] == "S"
        assert call_args["setup_type"] == "sweep_reversal"
        assert call_args["decision_snapshot_id"] == "a1b2c3d4e5f60718293a4b5c"
        assert call_args["setup_timeframe"] == "15m"
        assert call_args["ai_scalper_approved"] is True
        assert call_args["ai_scalper_verdict"] == "COHERENT"
        assert call_args["ai_scalper_action"] == "ENTER_NOW"


@pytest.mark.asyncio
async def test_auto_pilot_blocked_when_disabled(mock_mtf):
    service = MarketMonitor()

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": False,
    }):
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT",
            direction="long",
            m_type="stock",
            ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal,
            live_price=500.0,
            strat_res=mock_mtf.strategy,
            confluence=85,
            entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )
        assert result is None


@pytest.mark.asyncio
async def test_auto_pilot_15m_agile_mode_triggers_without_4h(mock_mtf):
    service = MarketMonitor()

    # 4H Bias is NOT ready (forming / neutral)
    mock_mtf.stages["bias"].status = "forming"

    # When mtf_hierarchy_required is False (Fast 15M Agile Mode)
    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_min_grade": "A",
        "auto_trade_entry_type": "momentum_market",
        "auto_trade_cooldown_seconds": 300,
        "mtf_hierarchy_required": False,
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock(return_value={"id": "order-agile-123", "status": "filled"})) as mock_create, \
       patch("app.services.event_trigger.broadcast", AsyncMock()), \
       patch.object(service.notifier, "send_signal_alert", AsyncMock()):

        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT",
            direction="long",
            m_type="stock",
            ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal,
            live_price=500.0,
            strat_res=mock_mtf.strategy,
            confluence=85,
            entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

        assert result is not None
        assert result["id"] == "order-agile-123"
        assert mock_create.called


def test_auto_pilot_has_no_mtf_input_or_gate():
    import inspect

    parameters = inspect.signature(
        MarketMonitor._evaluate_and_execute_auto_pilot
    ).parameters

    assert "mtf" not in parameters


@pytest.mark.asyncio
async def test_auto_pilot_fast_mode_blocks_non_actionable_trigger(mock_mtf):
    service = MarketMonitor()
    mock_mtf.strategy.approved = False
    mock_mtf.strategy.direction = "wait"

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
    }), patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_create:
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT",
            direction="long",
            m_type="stock",
            ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal,
            live_price=500.0,
            strat_res=mock_mtf.strategy,
            confluence=85,
            entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )
    assert result is None
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_pilot_counts_pending_positions_and_prevents_duplicate(mock_mtf):
    service = MarketMonitor()
    pending = {"symbol": "MSFT", "status": "pending"}

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_min_grade": "A",
        "mtf_hierarchy_required": False,
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value={"trades": [pending]})), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_create:
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT",
            direction="long",
            m_type="stock",
            ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal,
            live_price=500.0,
            strat_res=mock_mtf.strategy,
            confluence=85,
            entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result is None
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_pilot_vetoed_by_ai_scalper_conflict(mock_mtf):
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {
        "status": "reviewed",
        "verdict": "CONFLICT",
        "execution_action": "VETO_BLOCKED",
        "scalper_bias": "NO_TRADE",
        "conflicts": ["Delta positive divergence into resistance zone", "Trap OB detected"],
    }

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_min_grade": "A",
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_create:

        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT",
            direction="long",
            m_type="stock",
            ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal,
            live_price=500.0,
            strat_res=mock_mtf.strategy,
            confluence=85,
            entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

        assert result is None
        mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_pilot_vetoed_by_ai_scalper_action_blocked(mock_mtf):
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {
        "status": "reviewed",
        "verdict": "COHERENT",
        "execution_action": "VETO_BLOCKED",
        "scalper_bias": "NO_TRADE",
        "reason": "AI Scalper identified structural trap; execution blocked",
    }

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_min_grade": "A",
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_create:

        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT",
            direction="long",
            m_type="stock",
            ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal,
            live_price=500.0,
            strat_res=mock_mtf.strategy,
            confluence=85,
            entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

        assert result is None
        mock_create.assert_not_awaited()
