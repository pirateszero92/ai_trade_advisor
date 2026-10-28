"""Tests for Autonomous Auto-Pilot Execution in EventTriggerService."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from app.core.tri_core_contract import TRI_CORE_SETUP_TYPES
from app.engines.smc_engine import SMCSignal
from app.engines.strategy_engine import StrategyResult
from app.services.event_trigger import MarketMonitor
from app.services.mtf_analysis import MTFAnalysis, TimeframeStage


@pytest.fixture(autouse=True)
def reset_auto_trade_history(monkeypatch):
    from app.core.config import get_settings
    from app.services.candidate_lifecycle import candidate_lifecycle
    from app.services.decision_trace import decision_trace
    from app.services.event_trigger import _AUTO_TRADE_HISTORY
    from app.services.execution_revalidator import execution_revalidator
    from app.services.execution_analysis import execution_analyses
    from app.services.persistent_risk_guard import persistent_risk_guard

    _AUTO_TRADE_HISTORY.clear()
    candidate_lifecycle._records.clear()
    candidate_lifecycle._current_by_symbol.clear()
    decision_trace._records.clear()
    decision_trace._loaded = True
    monkeypatch.setattr(decision_trace, "_write_sync", lambda: None)
    monkeypatch.setattr(get_settings(), "equity_auto_trade_enabled", True)

    def _validated(**_kwargs):
        return SimpleNamespace(
            passed=True,
            reason_code="PASSED",
            reasons=[],
            executable_entry=500.0,
            stop_loss=496.0,
            take_profit=516.0,
            order_type="market",
            structural_rr=4.0,
            executable_rr=3.5,
            minimum_executable_rr=2.0,
            quote_age_seconds=0.0,
            spread_pct=0.00002,
            extension_atr=0.0,
        )

    monkeypatch.setattr(execution_revalidator, "validate", _validated)
    monkeypatch.setattr(
        persistent_risk_guard,
        "evaluate_and_persist",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        persistent_risk_guard,
        "cluster_loss_cooldown",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        execution_analyses,
        "get_by_snapshot_id",
        lambda _snapshot_id: SimpleNamespace(
            systemic_risk={"status": "NOT_APPLICABLE", "execution_allowed": True},
            signal=SimpleNamespace(equilibrium=0.0),
        ),
    )
    yield
    _AUTO_TRADE_HISTORY.clear()
    candidate_lifecycle._records.clear()
    candidate_lifecycle._current_by_symbol.clear()


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
            "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
            "input_event_id": "sweep:MSFT:15m:test",
            "input_symbol": "MSFT",
            "input_exchange": "alpaca",
            "input_timeframe": "15m",
            "reason": "Delta absorption confirmed at discount",
            "conflicts": [],
            "evidence": ["SMC and CVD context are coherent"],
        },
        indicator_decision={"ready": True, "squeeze_bonus": 10, "squeeze_bonus_max": 10},
        tri_core_setup={"actionable": True, "direction": "long", "grade": "S",
                        "setup_type": "sweep_reversal",
                        "entry_policy": "market_eligible", "order_type": "market",
                        "event_id": "sweep:MSFT:15m:test",
                        "calibration_status": "walk_forward_validated",
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
@pytest.mark.parametrize("setup_type", sorted(TRI_CORE_SETUP_TYPES))
async def test_auto_pilot_hands_every_canonical_setup_type_to_oms(
    mock_mtf, setup_type
):
    service = MarketMonitor()
    mock_mtf.trigger_signal.tri_core_setup["setup_type"] = setup_type

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
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
        assert call_args["setup_type"] == setup_type
        assert call_args["decision_snapshot_id"] == "a1b2c3d4e5f60718293a4b5c"
        assert call_args["setup_timeframe"] == "15m"
        assert call_args["ai_review_status"] == "reviewed"
        assert call_args["ai_review_verdict"] == "COHERENT"
        assert call_args["ai_review_reason"] == "Delta absorption confirmed at discount"
        assert "ai_scalper_approved" not in call_args
        assert "ai_scalper_action" not in call_args


@pytest.mark.asyncio
async def test_auto_pilot_blocked_when_disabled(mock_mtf):
    service = MarketMonitor()

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": False,
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as place_order:
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
        place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_stock_auto_pilot_is_shadow_only_until_promoted(mock_mtf, monkeypatch):
    from app.core.config import get_settings

    service = MarketMonitor()
    monkeypatch.setattr(get_settings(), "equity_auto_trade_enabled", False)
    with patch(
        "app.services.event_trigger._get_cached_runtime_settings",
        return_value={"auto_trade_enabled": True},
    ), patch(
        "app.services.paper_oms.paper_oms.place_order", AsyncMock()
    ) as place_order:
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
    place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_pilot_15m_agile_mode_triggers_without_4h(mock_mtf):
    service = MarketMonitor()

    # 4H Bias is NOT ready (forming / neutral)
    mock_mtf.stages["bias"].status = "forming"

    # When mtf_hierarchy_required is False (Fast 15M Agile Mode)
    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
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

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
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

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
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
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_event_id": "sweep:MSFT:15m:test",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
        "conflicts": ["Delta positive divergence into resistance zone", "Trap OB detected"],
    }

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
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
async def test_auto_pilot_blocks_when_ai_review_is_unavailable(mock_mtf):
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {}
    unavailable = SimpleNamespace(signal=SimpleNamespace(ai_review={
        "status": "error",
        "verdict": "UNAVAILABLE",
        "error": "AI advisory unavailable or invalid",
    }))

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_min_grade": "A",
    }), patch("app.services.execution_analysis.execution_analyses.get", AsyncMock(return_value=object())), \
       patch("app.services.scanner_ai.scanner_ai.review", AsyncMock(return_value=unavailable)), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_create:
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=85, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result is None
    assert mock_mtf.trigger_signal.ai_review["verdict"] == "UNAVAILABLE"
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_pilot_blocks_when_ai_review_raises(mock_mtf):
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {}

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_min_grade": "A",
    }), patch("app.services.execution_analysis.execution_analyses.get", AsyncMock(side_effect=TimeoutError("AI timeout"))), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_create:
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=85, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result is None
    assert mock_mtf.trigger_signal.ai_review["status"] == "error"
    assert mock_mtf.trigger_signal.ai_review["verdict"] == "UNAVAILABLE"
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_pilot_advisory_mode_does_not_forge_ai_approval(mock_mtf):
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {}

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": False,
        "auto_trade_min_grade": "A",
        "risk_per_trade": 0.75,
        "auto_trade_cooldown_seconds": 300,
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock(return_value={"id": "order-advisory", "status": "filled"})) as mock_create, \
       patch("app.services.event_trigger.broadcast", AsyncMock()), \
       patch.object(service.notifier, "send_signal_alert", AsyncMock()):
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=85, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result is not None
    payload = mock_create.call_args[0][0]
    assert payload["ai_review_required"] is False
    assert payload["ai_review_status"] == "not_requested"
    assert payload["ai_review_verdict"] == "UNAVAILABLE"
    assert "ai_scalper_approved" not in payload
    assert "ai_scalper_action" not in payload

@pytest.mark.asyncio
async def test_auto_pilot_blocks_direction_mismatch_with_tri_core(mock_mtf):
    service = MarketMonitor()
    mock_mtf.trigger_signal.tri_core_setup["direction"] = "short"

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
    }), patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_create:
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=85, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result is None
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_ai_execution_fields_are_ignored(mock_mtf):
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review.update({
        "scalper_bias": "BEARISH_SCALP",
        "verdict": "COHERENT",
        "ai_scalper_approved": False,
        "execution_action": "VETO_BLOCKED",
    })

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_min_grade": "A",
        "risk_per_trade": 0.75,
        "auto_trade_cooldown_seconds": 300,
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock(return_value={"id": "authority-order"})) as mock_create, \
       patch("app.services.event_trigger.broadcast", AsyncMock()), \
       patch.object(service.notifier, "send_signal_alert", AsyncMock()):
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=85, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result is not None
    payload = mock_create.call_args[0][0]
    assert payload["direction"] == "long"
    assert payload["tri_core_direction"] == "long"
    assert payload["decision_authority"] == "tri_core_setup_engine"
    assert payload["ai_review_verdict"] == "COHERENT"
    assert "ai_scalper_approved" not in payload
    assert "ai_scalper_action" not in payload


@pytest.mark.asyncio
async def test_auto_pilot_blocks_when_execution_revalidation_rejects(mock_mtf, monkeypatch):
    service = MarketMonitor()
    from app.services.execution_revalidator import execution_revalidator

    monkeypatch.setattr(
        execution_revalidator,
        "validate",
        lambda **_kwargs: SimpleNamespace(
            passed=False,
            reason_code="NO_CHASE",
            reasons=["Price extended beyond no-chase threshold"],
        ),
    )

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_min_grade": "A",
        "risk_per_trade": 0.75,
        "auto_trade_cooldown_seconds": 300,
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_create:
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=85, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result is None
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_pilot_persistent_risk_halt_blocks_before_risk_and_oms(
    mock_mtf,
    monkeypatch,
):
    from app.services.persistent_risk_guard import persistent_risk_guard

    service = MarketMonitor()
    monkeypatch.setattr(
        persistent_risk_guard,
        "evaluate_and_persist",
        AsyncMock(return_value={
            "reason": "three_consecutive_stop_losses",
            "halted_until": "2026-09-23T00:00:00+00:00",
            "source_payload": {"consecutive_stop_losses": 3},
        }),
    )

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_min_grade": "A",
        "risk_per_trade": 0.75,
        "auto_trade_cooldown_seconds": 300,
        "target_rr": 2.0,
        "default_sl_pct": 1.0,
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={
           "total_equity": 100000.0,
           "initial_capital": 100000.0,
           "daily_pnl_pct": 0.0,
       })), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={
           "bid": 499.99,
           "ask": 500.0,
       }), \
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
async def test_auto_pilot_btc_market_anchor_blocks_altcoin_long_before_execution(
    mock_mtf,
    monkeypatch,
):
    from app.services.execution_analysis import execution_analyses

    service = MarketMonitor()
    monkeypatch.setattr(
        execution_analyses,
        "get_by_snapshot_id",
        lambda _snapshot_id: SimpleNamespace(
            systemic_risk={
                "status": "BLOCKED",
                "execution_allowed": False,
                "reason_code": "BTC_ACTIVE_DUMP",
                "reason": "BTC Market Anchor: BTC is in active dump",
            },
            signal=SimpleNamespace(equilibrium=0.0),
        ),
    )

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": False,
        "auto_trade_min_grade": "A",
        "risk_per_trade": 0.75,
        "auto_trade_cooldown_seconds": 300,
        "target_rr": 2.0,
        "default_sl_pct": 1.0,
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={
           "total_equity": 100000.0,
           "initial_capital": 100000.0,
           "daily_pnl_pct": 0.0,
       })), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={
           "bid": 4.19,
           "ask": 4.20,
       }), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_create:
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="NEAR/USDT",
            direction="long",
            m_type="crypto",
            ex="binance",
            ltf_sig=mock_mtf.trigger_signal,
            live_price=4.20,
            strat_res=mock_mtf.strategy,
            confluence=85,
            entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5d",
            setup_timeframe="15m",
        )

    assert result is None
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_pilot_fast_track_grade_s_bypasses_ai_latency(mock_mtf):
    """Verify that Grade S setups fast-track past synchronous AI review when configured."""
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {}
    mock_mtf.trigger_signal.tri_core_setup["grade"] = "S"

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_fast_track_grade_s": True,
        "auto_trade_min_grade": "A",
        "risk_per_trade": 0.75,
        "auto_trade_cooldown_seconds": 300,
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock(return_value={"id": "order-ft", "status": "filled"})) as mock_create, \
       patch("app.services.event_trigger.broadcast", AsyncMock()), \
       patch.object(service.notifier, "send_signal_alert", AsyncMock()), \
       patch("app.services.scanner_ai.scanner_ai.review", AsyncMock()) as mock_ai_review:
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=85, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result is not None
    # Fast track executed immediately without blocking on synchronous review
    mock_create.assert_awaited_once()
    assert mock_mtf.trigger_signal.ai_review["fast_tracked"] is True
    assert mock_mtf.trigger_signal.ai_review["verdict"] == "COHERENT"


@pytest.mark.asyncio
async def test_auto_pilot_soft_caution_proceeds_with_halved_position_risk(mock_mtf):
    """Verify that an AI CAUTION verdict (Soft Conflict) allows the trade with 0.5x position sizing."""
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {}
    mock_mtf.trigger_signal.tri_core_setup["grade"] = "A"
    caution_review = SimpleNamespace(signal=SimpleNamespace(ai_review={
        "status": "reviewed",
        "verdict": "CAUTION",
        "decision": "BUY",
        "reason": "Market thesis has slight qualitative hesitation but direction plausible",
        "conflicts": ["Derivatives positioning is crowded"],
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
    }))

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_ai_gate_hard": True,
        "auto_trade_accept_caution": True,
        "auto_trade_fast_track_grade_s": False,
        "auto_trade_soft_caution_sizing_pct": 0.5,
        "auto_trade_min_grade": "A",
        "risk_per_trade": 0.75,
        "auto_trade_cooldown_seconds": 300,
    }), patch("app.services.execution_analysis.execution_analyses.get", AsyncMock(return_value=object())), \
       patch("app.services.scanner_ai.scanner_ai.review", AsyncMock(return_value=caution_review)), \
       patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock(return_value={"id": "order-caution", "status": "filled"})) as mock_create, \
       patch("app.services.event_trigger.broadcast", AsyncMock()), \
       patch.object(service.notifier, "send_signal_alert", AsyncMock()):
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=75, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result is not None
    mock_create.assert_awaited_once()
    order_payload = mock_create.call_args[0][0]
    assert order_payload["ai_review_verdict"] == "CAUTION"
    # Full normal risk would be 135 units, halved risk is ~67.5 -> 67 units
    assert order_payload["position_size"] < 100.0


@pytest.mark.asyncio
async def test_auto_pilot_soft_caution_blocked_by_default_hard_gate(mock_mtf):
    """Verify that an AI CAUTION verdict is strictly blocked by default when auto_trade_accept_caution is False."""
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {}
    mock_mtf.trigger_signal.tri_core_setup["grade"] = "A"
    caution_review = SimpleNamespace(signal=SimpleNamespace(ai_review={
        "status": "reviewed",
        "verdict": "CAUTION",
        "decision": "BUY",
        "reason": "Market thesis has slight qualitative hesitation",
        "conflicts": ["Candidate is located in DISCOUNT"],
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
    }))

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_ai_gate_hard": True,
        "auto_trade_accept_caution": False,
        "auto_trade_fast_track_grade_s": False,
        "auto_trade_soft_caution_sizing_pct": 0.5,
        "auto_trade_min_grade": "A",
        "risk_per_trade": 0.75,
        "auto_trade_cooldown_seconds": 300,
    }), patch("app.services.execution_analysis.execution_analyses.get", AsyncMock(return_value=object())), \
       patch("app.services.scanner_ai.scanner_ai.review", AsyncMock(return_value=caution_review)), \
       patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_create, \
       patch("app.services.event_trigger.broadcast", AsyncMock()), \
       patch.object(service.notifier, "send_signal_alert", AsyncMock()):
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=75, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result is None
    mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_auto_pilot_hard_conflict_blocks_trade(mock_mtf):
    """Verify that an AI CONFLICT verdict (Hard Conflict) strictly blocks execution."""
    service = MarketMonitor()
    mock_mtf.trigger_signal.tri_core_setup["grade"] = "A"
    conflict_review = SimpleNamespace(signal=SimpleNamespace(ai_review={
        "status": "reviewed",
        "verdict": "CONFLICT",
        "reason": "Direct factual contradiction: opposing CVD divergence and adverse location",
        "conflicts": ["Opposing CVD divergence", "Candidate is located in DISCOUNT"],
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
    }))

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_ai_gate_hard": True,
        "auto_trade_fast_track_grade_s": False,
        "auto_trade_min_grade": "A",
        "risk_per_trade": 0.75,
        "auto_trade_cooldown_seconds": 300,
    }), patch("app.services.execution_analysis.execution_analyses.get", AsyncMock(return_value=object())), \
       patch("app.services.scanner_ai.scanner_ai.review", AsyncMock(return_value=conflict_review)), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_create:
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=75, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result is None
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_tri_core_to_ai_decision_streamlined_execution(mock_mtf):
    service = MarketMonitor()
    service.paper_oms = True

    # 1. AI Decision BUY -> Trade executes
    buy_review = SimpleNamespace(signal=SimpleNamespace(ai_review={
        "status": "reviewed",
        "verdict": "COHERENT",
        "decision": "BUY",
        "reason": "AI approved long based on clean sweep and CVD accumulation",
        "conflicts": [],
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
    }))

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_ai_gate_hard": True,
        "auto_trade_fast_track_grade_s": False,
        "auto_trade_min_grade": "A",
        "risk_per_trade": 0.75,
        "auto_trade_cooldown_seconds": 300,
    }), patch("app.services.execution_analysis.execution_analyses.get", AsyncMock(return_value=object())), \
       patch("app.services.scanner_ai.scanner_ai.review", AsyncMock(return_value=buy_review)), \
       patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock(return_value={"id": "t-123", "status": "filled"})) as mock_place, \
       patch("app.services.event_trigger.broadcast", AsyncMock()), \
       patch.object(service.notifier, "send_signal_alert", AsyncMock()):
        result = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=75, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result == {"id": "t-123", "status": "filled"}
    mock_place.assert_awaited_once()

    # 2. AI Decision PASS -> Trade withheld
    pass_review = SimpleNamespace(signal=SimpleNamespace(ai_review={
        "status": "reviewed",
        "verdict": "CONFLICT",
        "decision": "PASS",
        "reason": "AI decided PASS due to high macro volatility",
        "conflicts": ["Macro volatility"],
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
    }))

    with patch("app.core.runtime_config.load_runtime_config", return_value={"risk_per_trade": 0.75}), \
         patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_require_ai_approval": True,
        "auto_trade_ai_gate_hard": True,
        "auto_trade_fast_track_grade_s": False,
        "auto_trade_min_grade": "A",
        "risk_per_trade": 0.75,
        "auto_trade_cooldown_seconds": 300,
    }), patch("app.services.execution_analysis.execution_analyses.get", AsyncMock(return_value=object())), \
       patch("app.services.scanner_ai.scanner_ai.review", AsyncMock(return_value=pass_review)), \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock()) as mock_place_2:
        result_pass = await service._evaluate_and_execute_auto_pilot(
            symbol="MSFT", direction="long", m_type="stock", ex="alpaca",
            ltf_sig=mock_mtf.trigger_signal, live_price=500.0,
            strat_res=mock_mtf.strategy, confluence=75, entry_mode="limit",
            decision_snapshot_id="a1b2c3d4e5f60718293a4b5c",
            setup_timeframe="15m",
        )

    assert result_pass is None
    mock_place_2.assert_not_awaited()


