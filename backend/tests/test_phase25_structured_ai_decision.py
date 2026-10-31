import pytest
from app.services.scanner_ai import (
    Proposal,
    SetupEvaluation,
    MarketContext,
    ExecutionAdvice,
    validate_advisory,
)
from app.services.authority_status import build_authority_status
from app.services.decision_trace import decision_trace
from tests.test_scanner_ai import snapshot


def test_phase3_proposal_with_complete_structured_fields():
    prop = Proposal.model_validate({
        "verdict": "COHERENT",
        "decision": "BUY",
        "reason": "โครงสร้าง 15m มี liquidity sweep และ CVD ยืนยันชัดเจน",
        "conflicts": [],
        "evidence": ["sweep.reclaim", "cvd.absorption"],
        "risk_rating": "LOW",
        "risk_score": 15,
        "suggested_risk_multiplier": 1.0,
        "setup_evaluation": {
            "structure_quality": "HIGH",
            "location_assessment": "DISCOUNT",
            "flow_alignment": "STRONG_ALIGNED",
        },
        "market_context": {
            "regime_alignment": "SUPPORTIVE",
            "volatility_assessment": "NORMAL",
            "macro_sentiment": "BULLISH",
        },
        "execution_advice": {
            "suggested_timing": "IMMEDIATE",
            "invalidation_thesis": "หลุด swing low 15m ปิดแท่ง",
            "management_plan": "TP1 ที่ 1.5R แบ่งทำกำไร 50% แล้วเลื่อน BE",
        },
    })
    dumped = prop.model_dump()
    assert dumped["verdict"] == "COHERENT"
    assert dumped["risk_rating"] == "LOW"
    assert dumped["risk_score"] == 15
    assert dumped["suggested_risk_multiplier"] == 1.0
    assert dumped["setup_evaluation"]["structure_quality"] == "HIGH"
    assert dumped["market_context"]["macro_sentiment"] == "BULLISH"
    assert dumped["execution_advice"]["suggested_timing"] == "IMMEDIATE"


def test_phase3_proposal_backward_compatibility():
    # Legacy / minimal input without Phase 3 keys
    prop = Proposal.model_validate({
        "verdict": "CAUTION",
        "decision": "BUY",
        "reason": "มี flow ambiguity เล็กน้อย",
        "conflicts": ["flow minor"],
    })
    dumped = prop.model_dump()
    assert dumped["verdict"] == "CAUTION"
    assert dumped["risk_rating"] == "MODERATE"
    assert dumped["risk_score"] == 50
    assert dumped["suggested_risk_multiplier"] == 1.0
    assert "setup_evaluation" in dumped
    assert "market_context" in dumped
    assert "execution_advice" in dumped


def test_phase3_proposal_drops_forbidden_authority_fields():
    prop = Proposal.model_validate({
        "verdict": "COHERENT",
        "decision": "BUY",
        "reason": "test",
        "direction": "short",
        "entry": 123.45,
        "stop_loss": 100.0,
        "take_profit": 200.0,
        "risk_pct": 5.0,
        "position_size": 1000,
        "leverage": 50,
        "confidence": 0.99,
    })
    dumped = prop.model_dump()
    for forbidden in (
        "direction",
        "entry",
        "stop_loss",
        "take_profit",
        "risk_pct",
        "position_size",
        "leverage",
        "confidence",
    ):
        assert forbidden not in dumped


def test_phase3_validate_advisory_deterministic_clamps_for_coherent():
    analysis = snapshot()
    analysis.signal.tri_core_setup.update({
        "actionable": True,
        "direction": "long",
        "entry_policy": "market_eligible",
    })
    prop = Proposal.model_validate({
        "verdict": "COHERENT",
        "decision": "BUY",
        "reason": "โครงสร้างสมบูรณ์",
        "risk_rating": "HIGH",  # AI hallucinates HIGH rating for COHERENT
        "risk_score": 90,       # AI hallucinates 90 for COHERENT
        "suggested_risk_multiplier": 1.5,  # AI attempts to increase risk > 1.0
        "execution_advice": {
            "suggested_timing": "IMMEDIATE",
        },
    })
    result = validate_advisory(prop, {}, analysis)
    # Guaranteed clamped safely
    assert result["verdict"] == "COHERENT"
    assert result["risk_rating"] == "LOW"
    assert result["risk_score"] <= 40
    assert result["suggested_risk_multiplier"] == 1.0
    assert result["execution_advice"]["suggested_timing"] == "IMMEDIATE"


def test_phase3_validate_advisory_deterministic_clamps_for_caution():
    analysis = snapshot()
    analysis.signal.tri_core_setup.update({
        "actionable": True,
        "direction": "long",
        "entry_policy": "limit_retest_only",
    })
    # Set zone in equilibrium so deterministic gate enforces CAUTION
    analysis.signal.in_equilibrium = True
    prop = Proposal.model_validate({
        "verdict": "COHERENT",  # Will be downgraded to CAUTION by deterministic gate
        "decision": "BUY",
        "reason": "test",
        "suggested_risk_multiplier": 1.0,  # AI attempted 1.0 on equilibrium
        "execution_advice": {
            "suggested_timing": "IMMEDIATE",  # Tri-Core is limit_retest_only!
        },
    })
    result = validate_advisory(prop, {}, analysis)
    assert result["verdict"] == "CAUTION"
    assert result["risk_rating"] == "MODERATE"
    assert 41 <= result["risk_score"] <= 75
    # Caution strictly clamps risk multiplier to <= 0.5
    assert result["suggested_risk_multiplier"] == 0.5
    # Tri-Core limit_retest_only clamps IMMEDIATE -> LIMIT_RETEST_ONLY
    assert result["execution_advice"]["suggested_timing"] == "LIMIT_RETEST_ONLY"


def test_phase3_validate_advisory_deterministic_clamps_for_conflict():
    analysis = snapshot()
    analysis.signal.tri_core_setup.update({
        "actionable": True,
        "direction": "long",
        "entry_policy": "market_eligible",
    })
    prop = Proposal.model_validate({
        "verdict": "CONFLICT",
        "decision": "PASS",
        "reason": "opposing structure",
        "suggested_risk_multiplier": 0.5,
        "execution_advice": {
            "suggested_timing": "IMMEDIATE",
        },
    })
    result = validate_advisory(prop, {}, analysis)
    assert result["verdict"] == "CONFLICT"
    assert result["suggested_risk_multiplier"] == 0.0
    assert result["risk_score"] >= 80
    assert result["execution_advice"]["suggested_timing"] == "STAND_ASIDE"


@pytest.mark.anyio
async def test_phase3_authority_status_projection_includes_decision_manager_fields():
    await decision_trace.record(
        snapshot_id="snap-p3",
        event_id="event-p3",
        symbol="BTC/USDT",
        stage="AI_REVIEW",
        facts={
            "status": "reviewed",
            "verdict": "COHERENT",
            "decision": "BUY",
            "risk_rating": "LOW",
            "risk_score": 25,
            "suggested_risk_multiplier": 1.0,
            "reason": "All systems aligned",
        },
    )
    view = await build_authority_status(
        symbol="BTC/USDT",
        snapshot_id="snap-p3",
        event_id="event-p3",
        tri_core_setup={"actionable": True, "direction": "long", "grade": "A"},
        structural_rr=2.5,
        ai_review={
            "status": "reviewed",
            "verdict": "COHERENT",
            "decision": "BUY",
            "risk_rating": "LOW",
            "risk_score": 25,
            "suggested_risk_multiplier": 1.0,
            "setup_evaluation": {"structure_quality": "HIGH"},
            "market_context": {"macro_sentiment": "BULLISH"},
            "execution_advice": {"suggested_timing": "IMMEDIATE"},
        },
    )
    ai_sec = view["ai_review"]
    assert ai_sec["decision"] == "BUY"
    assert ai_sec["risk_rating"] == "LOW"
    assert ai_sec["risk_score"] == 25
    assert ai_sec["suggested_risk_multiplier"] == 1.0
    assert ai_sec["setup_evaluation"]["structure_quality"] == "HIGH"
    assert ai_sec["market_context"]["macro_sentiment"] == "BULLISH"
    assert ai_sec["execution_advice"]["suggested_timing"] == "IMMEDIATE"

    # Stage view
    stage_ai = view["stages"]["ai_review"]
    assert stage_ai["decision"] == "BUY"
    assert stage_ai["risk_score"] == 25
    assert stage_ai["suggested_risk_multiplier"] == 1.0


# ---------------------------------------------------------------------------
# Finding P2 Integration Tests: _evaluate_and_execute_auto_pilot guardrails
# ---------------------------------------------------------------------------
from unittest.mock import AsyncMock, patch
from app.services.event_trigger import MarketMonitor
from app.services.candidate_lifecycle import candidate_lifecycle, CandidateState
from types import SimpleNamespace
from app.engines.smc_engine import SMCSignal
from app.engines.strategy_engine import StrategyResult
from app.services.mtf_analysis import MTFAnalysis, TimeframeStage


@pytest.fixture(autouse=True)
def reset_auto_trade_history(monkeypatch):
    from app.core.config import get_settings
    from app.services.candidate_lifecycle import candidate_lifecycle
    from app.services.decision_trace import decision_trace
    from app.services.execution_analysis import execution_analyses

    candidate_lifecycle._records.clear()
    candidate_lifecycle._current_by_symbol.clear()
    decision_trace._records.clear()
    decision_trace._loaded = True
    monkeypatch.setattr(decision_trace, "_write_sync", lambda: None)
    monkeypatch.setattr(get_settings(), "equity_auto_trade_enabled", True)
    monkeypatch.setattr(
        execution_analyses,
        "get_by_snapshot_id",
        lambda _snapshot_id: SimpleNamespace(
            systemic_risk={"status": "NOT_APPLICABLE", "execution_allowed": True},
            signal=SimpleNamespace(equilibrium=0.0),
        ),
    )
    yield
    candidate_lifecycle._records.clear()
    candidate_lifecycle._current_by_symbol.clear()


@pytest.fixture
def mock_mtf():
    sig_4h = SMCSignal(symbol="MSFT", timeframe="4h", bias="bullish", confluence=80)
    stage_4h = TimeframeStage(role="bias", timeframe="4h", status="ready", direction="long", signal=sig_4h)

    sig_1h = SMCSignal(symbol="MSFT", timeframe="1h", bias="bullish", confluence=75)
    stage_1h = TimeframeStage(role="setup", timeframe="1h", status="ready", direction="long", signal=sig_1h)

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

    return SimpleNamespace(
        symbol="MSFT",
        trigger_signal=sig_15m,
        strategy=strat_res,
        ai_review={"verdict": "COHERENT", "confidence": 0.95},
    )


@pytest.mark.asyncio
@pytest.mark.skip(reason='Legacy pre-streamlined AI veto test; Tri-Core is now sole deterministic authority')
async def test_auto_pilot_blocks_conflict_even_under_soft_gate(mock_mtf):
    """P1 Integration 1: CONFLICT + soft gate (auto_trade_ai_gate_hard=False) must block execution."""
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {
        "status": "reviewed",
        "verdict": "CONFLICT",
        "decision": "PASS",
        "suggested_risk_multiplier": 0.0,
        "execution_advice": {"suggested_timing": "STAND_ASIDE"},
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_event_id": "sweep:MSFT:15m:test",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
        "conflicts": ["Adverse momentum against long"],
    }

    await candidate_lifecycle.register(
        symbol="MSFT",
        snapshot_id="a1b2c3d4e5f60718293a4b5c",
        event_id="sweep:MSFT:15m:test",
        ready=True,
    )

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_ai_gate_hard": False,  # Soft gate!
        "auto_trade_require_ai_approval": True,
        "auto_trade_min_grade": "A",
        "auto_trade_entry_type": "momentum_market",
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
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
        assert not mock_create.called
        current = await candidate_lifecycle.current("MSFT")
        assert current is not None
        assert current["state"] == CandidateState.AI_CONFLICT.value


@pytest.mark.asyncio
@pytest.mark.skip(reason='Legacy pre-streamlined AI veto test; Tri-Core is now sole deterministic authority')
async def test_auto_pilot_blocks_unavailable_even_under_soft_gate(mock_mtf):
    """P1 Integration 2: UNAVAILABLE + soft gate must block execution."""
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {
        "status": "error",
        "verdict": "UNAVAILABLE",
        "decision": "PASS",
        "suggested_risk_multiplier": 0.0,
        "execution_advice": {"suggested_timing": "STAND_ASIDE"},
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_event_id": "sweep:MSFT:15m:test",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
    }

    await candidate_lifecycle.register(
        symbol="MSFT",
        snapshot_id="a1b2c3d4e5f60718293a4b5c",
        event_id="sweep:MSFT:15m:test",
        ready=True,
    )

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_ai_gate_hard": False,  # Soft gate!
        "auto_trade_require_ai_approval": True,
        "auto_trade_min_grade": "A",
        "auto_trade_entry_type": "momentum_market",
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
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
        assert not mock_create.called
        current = await candidate_lifecycle.current("MSFT")
        assert current is not None
        assert current["state"] == CandidateState.AI_UNAVAILABLE.value


@pytest.mark.asyncio
@pytest.mark.skip(reason='Legacy pre-streamlined AI veto test; Tri-Core is now sole deterministic authority')
async def test_auto_pilot_blocks_stand_aside_advice(mock_mtf):
    """P1 Integration 3: suggested_timing STAND_ASIDE blocks execution."""
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {
        "status": "reviewed",
        "verdict": "COHERENT",
        "decision": "BUY",
        "suggested_risk_multiplier": 1.0,
        "execution_advice": {"suggested_timing": "STAND_ASIDE"},
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_event_id": "sweep:MSFT:15m:test",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
    }

    await candidate_lifecycle.register(
        symbol="MSFT",
        snapshot_id="a1b2c3d4e5f60718293a4b5c",
        event_id="sweep:MSFT:15m:test",
        ready=True,
    )

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_min_grade": "A",
        "auto_trade_entry_type": "momentum_market",
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
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
        assert not mock_create.called
        current = await candidate_lifecycle.current("MSFT")
        assert current is not None
        assert current["state"] == CandidateState.RISK_REJECTED.value


@pytest.mark.asyncio
@pytest.mark.skip(reason='Legacy pre-streamlined AI veto test; Tri-Core is now sole deterministic authority')
async def test_auto_pilot_defers_wait_confirmation_advice(mock_mtf):
    """P1 Integration 4: suggested_timing WAIT_CONFIRMATION defers to WAITING_ENTRY."""
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {
        "status": "reviewed",
        "verdict": "COHERENT",
        "decision": "BUY",
        "suggested_risk_multiplier": 1.0,
        "execution_advice": {"suggested_timing": "WAIT_CONFIRMATION"},
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_event_id": "sweep:MSFT:15m:test",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
    }

    await candidate_lifecycle.register(
        symbol="MSFT",
        snapshot_id="a1b2c3d4e5f60718293a4b5c",
        event_id="sweep:MSFT:15m:test",
        ready=True,
    )

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_min_grade": "A",
        "auto_trade_entry_type": "momentum_market",
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
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
        assert not mock_create.called
        current = await candidate_lifecycle.current("MSFT")
        assert current is not None
        assert current["state"] == CandidateState.WAITING_ENTRY.value


@pytest.mark.asyncio
@pytest.mark.skip(reason='Legacy pre-streamlined AI veto test; Tri-Core is now sole deterministic authority')
async def test_auto_pilot_timing_limit_retest_only_defers_market_order(mock_mtf):
    """P1 Integration 5: AI timing LIMIT_RETEST_ONLY blocks immediate market execution."""
    service = MarketMonitor()
    mock_mtf.trigger_signal.ai_review = {
        "status": "reviewed",
        "verdict": "COHERENT",
        "decision": "BUY",
        "suggested_risk_multiplier": 1.0,
        "execution_advice": {"suggested_timing": "LIMIT_RETEST_ONLY"},
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_event_id": "sweep:MSFT:15m:test",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
    }

    await candidate_lifecycle.register(
        symbol="MSFT",
        snapshot_id="a1b2c3d4e5f60718293a4b5c",
        event_id="sweep:MSFT:15m:test",
        ready=True,
    )

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_min_grade": "A",
        "auto_trade_entry_type": "momentum_market",
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
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
        assert not mock_create.called
        current = await candidate_lifecycle.current("MSFT")
        assert current is not None
        assert current["state"] == CandidateState.WAITING_ENTRY.value


@pytest.mark.asyncio
@pytest.mark.skip(reason='Legacy pre-streamlined AI veto test; Tri-Core is now sole deterministic authority')
async def test_auto_pilot_applies_ai_risk_multiplier_to_risk_engine(mock_mtf):
    """P1 Integration 6: AI suggested_risk_multiplier is applied directly to risk_per_trade_pct."""
    from app.engines.risk_engine import squeeze_adjusted_risk_pct

    service = MarketMonitor()
    # Caution with custom 0.3x multiplier
    mock_mtf.trigger_signal.ai_review = {
        "status": "reviewed",
        "verdict": "CAUTION",
        "decision": "BUY",
        "suggested_risk_multiplier": 0.3,
        "execution_advice": {"suggested_timing": "IMMEDIATE"},
        "input_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "input_event_id": "sweep:MSFT:15m:test",
        "input_symbol": "MSFT",
        "input_exchange": "alpaca",
        "input_timeframe": "15m",
    }

    await candidate_lifecycle.register(
        symbol="MSFT",
        snapshot_id="a1b2c3d4e5f60718293a4b5c",
        event_id="sweep:MSFT:15m:test",
        ready=True,
    )

    with patch("app.services.event_trigger._get_cached_runtime_settings", return_value={
        "auto_trade_enabled": True,
        "auto_trade_accept_caution": True,
        "auto_trade_soft_caution_sizing_pct": 0.5,
        "auto_trade_min_grade": "A",
        "auto_trade_entry_type": "momentum_market",
    }), patch("app.services.paper_oms.paper_oms.ready", True), \
       patch("app.services.paper_oms.paper_oms.list_positions", AsyncMock(return_value=[])), \
       patch("app.services.paper_oms.paper_oms.account_snapshot", AsyncMock(return_value={"total_equity": 100000.0})), \
       patch("app.services.paper_oms.paper_oms.quote_snapshot", return_value={"bid": 499.99, "ask": 500.0}), \
       patch.object(service.risk, "evaluate", wraps=service.risk.evaluate) as spy_risk, \
       patch("app.services.paper_oms.paper_oms.place_order", AsyncMock(return_value={"id": "order-p3", "status": "filled"})):

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
        assert spy_risk.called
        call_kwargs = spy_risk.call_args[1]
        passed_risk_pct = call_kwargs["risk_per_trade_pct"]
        expected_risk = squeeze_adjusted_risk_pct(mock_mtf.trigger_signal) * 0.3
        assert pytest.approx(passed_risk_pct, rel=1e-2) == expected_risk

