import pytest

from app.services.authority_status import build_authority_status
from app.services.candidate_lifecycle import CandidateState, candidate_lifecycle
from app.services.decision_trace import decision_trace


@pytest.fixture(autouse=True)
async def reset_state(monkeypatch):
    await candidate_lifecycle.reset()
    decision_trace._records.clear()
    decision_trace._loaded = True
    monkeypatch.setattr(decision_trace, "_write_sync", lambda: None)
    yield
    await candidate_lifecycle.reset()
    decision_trace._records.clear()


@pytest.mark.anyio
async def test_phase23_projection_separates_all_authority_stages():
    await candidate_lifecycle.register(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        ready=True,
    )
    await candidate_lifecycle.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        new_state=CandidateState.AI_REVIEW_PENDING,
    )
    await candidate_lifecycle.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        new_state=CandidateState.AI_COHERENT,
    )
    await candidate_lifecycle.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        new_state=CandidateState.EXECUTION_VALIDATING,
    )
    await candidate_lifecycle.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        new_state=CandidateState.EXECUTION_VALIDATED,
    )
    await candidate_lifecycle.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        new_state=CandidateState.RISK_APPROVED,
    )

    for stage, facts in (
        ("AI_REVIEW", {"status": "reviewed", "verdict": "COHERENT", "reason": "No contradiction"}),
        ("EXECUTION_REVALIDATION", {
            "passed": True,
            "reason_code": "OK",
            "structural_rr": 3.0,
            "executable_rr": 2.4,
            "minimum_executable_rr": 2.0,
            "quote_age_seconds": 0.7,
            "spread_pct": 0.04,
        }),
        ("RISK", {
            "approved": True,
            "risk_pct": 0.75,
            "risk_amount": 75.0,
            "position_size": 1.25,
            "sizing_authority": "risk_engine",
        }),
        ("OMS_RESULT", {"status": "open", "fill_price": 100.1, "filled_quantity": 1.25}),
    ):
        await decision_trace.record(
            snapshot_id="snap-1",
            event_id="event-1",
            symbol="BTC/USDT",
            stage=stage,
            facts=facts,
        )

    view = await build_authority_status(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        tri_core_setup={
            "actionable": True,
            "direction": "long",
            "grade": "A",
            "setup_type": "sweep_reversal",
        },
        structural_rr=3.0,
    )

    assert view["tri_core"]["status"] == "ready"
    assert view["ai_review"]["verdict"] == "COHERENT"
    assert view["execution"]["status"] == "validated"
    assert view["execution"]["structural_rr"] == 3.0
    assert view["execution"]["executable_rr"] == 2.4
    assert view["execution"]["quote_freshness"] == "fresh"
    assert view["risk"]["status"] == "approved"
    assert view["risk"]["sizing_authority"] == "risk_engine"
    assert view["order"]["ready"] is False
    assert view["order"]["status"] == "open"
    assert view["oms"]["status"] == "open"
    assert view["lifecycle_state"] == "OPEN"


@pytest.mark.anyio
async def test_pending_ai_review_exposes_live_pipeline_stage():
    await candidate_lifecycle.register(
        symbol="BTC/USDT",
        snapshot_id="snap-progress",
        event_id="event-progress",
        ready=True,
    )
    await candidate_lifecycle.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-progress",
        event_id="event-progress",
        new_state=CandidateState.AI_REVIEW_PENDING,
    )
    await decision_trace.record(
        snapshot_id="snap-progress",
        event_id="event-progress",
        symbol="BTC/USDT",
        stage="AI_REVIEW",
        facts={
            "status": "pending",
            "verdict": "PENDING",
            "review_stage": "BLIND_READING",
            "input_snapshot_id": "snap-progress",
        },
    )

    view = await build_authority_status(
        symbol="BTC/USDT",
        snapshot_id="snap-progress",
        event_id="event-progress",
        tri_core_setup={
            "actionable": True,
            "direction": "long",
            "grade": "A",
            "setup_type": "sweep_reversal",
        },
        structural_rr=2.5,
    )

    assert view["tri_core"]["status"] == "ready"
    assert view["ai_review"]["status"] == "pending"
    assert view["ai_review"]["review_stage"] == "BLIND_READING"


@pytest.mark.anyio
async def test_systemic_risk_blocks_order_without_erasing_valid_tricore_setup():
    await decision_trace.record(
        snapshot_id="snap-systemic",
        event_id="event-systemic",
        symbol="ETH/USDT",
        stage="SYSTEMIC_RISK",
        facts={
            "status": "BLOCKED",
            "execution_allowed": False,
            "reason_code": "BTC_ACTIVE_DUMP",
            "reason": "BTC is in active dump",
            "btc_snapshot_id": "btc-snap-1",
        },
    )
    view = await build_authority_status(
        symbol="ETH/USDT",
        snapshot_id="snap-systemic",
        event_id="event-systemic",
        tri_core_setup={
            "actionable": True,
            "direction": "long",
            "grade": "A",
            "setup_type": "sweep_reversal",
        },
        structural_rr=3.0,
    )

    assert view["tri_core"]["status"] == "ready"
    assert view["systemic_risk"]["execution_allowed"] is False
    assert view["risk"]["status"] == "blocked"
    assert view["risk"]["risk_guard"] == "btc_systemic_anchor"
    assert view["order"]["ready"] is False


@pytest.mark.anyio
async def test_closed_oms_state_overrides_stale_open_candidate_and_stays_locked():
    await candidate_lifecycle.register(
        symbol="NEAR/USDT",
        snapshot_id="snap-closed",
        event_id="event-closed",
        ready=True,
    )
    for state in (
        CandidateState.AI_REVIEW_PENDING,
        CandidateState.AI_COHERENT,
        CandidateState.EXECUTION_VALIDATING,
        CandidateState.EXECUTION_VALIDATED,
        CandidateState.RISK_APPROVED,
        CandidateState.ORDER_PENDING,
        CandidateState.OPEN,
    ):
        assert await candidate_lifecycle.transition(
            symbol="NEAR/USDT",
            snapshot_id="snap-closed",
            event_id="event-closed",
            new_state=state,
        )

    await decision_trace.record(
        snapshot_id="snap-closed",
        event_id="event-closed",
        symbol="NEAR/USDT",
        stage="OMS_STATE",
        facts={
            "status": "closed",
            "fill_price": 4.496512,
            "close_reason": "Stop Loss (SL Hit)",
        },
    )

    view = await build_authority_status(
        symbol="NEAR/USDT",
        snapshot_id="snap-closed",
        event_id="event-closed",
        tri_core_setup={
            "actionable": True,
            "direction": "short",
            "grade": "A",
            "setup_type": "range_boundary_ping_pong",
        },
        structural_rr=3.4,
    )

    assert view["lifecycle_state"] == "CLOSED"
    assert view["lifecycle_reason_code"] == "OMS_CLOSED"
    assert view["lifecycle_reason"] == "Stop Loss (SL Hit)"
    assert view["oms"]["status"] == "closed"
    assert view["order"]["status"] == "closed"
    assert view["order"]["ready"] is False
    assert view["order"]["manual_override_required"] is False


@pytest.mark.anyio
async def test_phase23_stale_quote_is_explicit_not_hidden_in_combined_score():
    await decision_trace.record(
        snapshot_id="snap-2",
        event_id="event-2",
        symbol="MSFT",
        stage="EXECUTION_REVALIDATION",
        facts={
            "passed": False,
            "reason_code": "QUOTE_STALE",
            "quote_age_seconds": 31.0,
            "structural_rr": 2.8,
            "executable_rr": None,
        },
    )

    view = await build_authority_status(
        symbol="MSFT",
        snapshot_id="snap-2",
        event_id="event-2",
        tri_core_setup={"actionable": True, "direction": "long", "grade": "A"},
        structural_rr=2.8,
    )

    assert view["execution"]["status"] == "rejected"
    assert view["execution"]["reason_code"] == "QUOTE_STALE"
    assert view["execution"]["quote_freshness"] == "stale"
    assert view["tri_core"]["structural_rr"] == 2.8
    assert view["order"]["ready"] is False
    assert "score" not in view


@pytest.mark.anyio
async def test_authority_projection_rejects_ai_review_from_another_snapshot():
    await decision_trace.record(
        snapshot_id="snap-current",
        event_id="event-current",
        symbol="DOT/USDT",
        stage="AI_REVIEW",
        facts={
            "status": "reviewed",
            "verdict": "COHERENT",
            "input_snapshot_id": "snap-old",
        },
    )

    view = await build_authority_status(
        symbol="DOT/USDT",
        snapshot_id="snap-current",
        event_id="event-current",
        tri_core_setup={"actionable": True, "direction": "short", "grade": "A"},
        structural_rr=3.15,
    )

    assert view["ai_review"]["verdict"] == "UNAVAILABLE"
    assert view["ai_review"]["failure_type"] == "STALE_REVIEW"
    assert view["ai_review"]["snapshot_matches"] is False
    assert view["order"]["ready"] is False


@pytest.mark.anyio
async def test_order_ready_requires_all_stages_and_no_existing_submission():
    for stage, facts in (
        ("AI_REVIEW", {
            "status": "reviewed",
            "verdict": "COHERENT",
            "input_snapshot_id": "snap-ready",
        }),
        ("EXECUTION_REVALIDATION", {"passed": True, "reason_code": "OK"}),
        ("RISK", {"approved": True, "position_size": 2.0}),
    ):
        await decision_trace.record(
            snapshot_id="snap-ready",
            event_id="event-ready",
            symbol="SOL/USDT",
            stage=stage,
            facts=facts,
        )

    view = await build_authority_status(
        symbol="SOL/USDT",
        snapshot_id="snap-ready",
        event_id="event-ready",
        tri_core_setup={"actionable": True, "direction": "long", "grade": "A"},
        structural_rr=2.5,
    )

    assert view["order"] == {
        "status": "ready",
        "ready": True,
        "manual_override_required": False,
    }


@pytest.mark.anyio
async def test_chart_overlay_restores_exact_authority_projection(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import pandas as pd

    from app.api import chart as chart_api
    from app.engines.smc_engine import SMCSignal
    from app.engines.strategy_engine import StrategyResult

    frame = pd.DataFrame(
        [{
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
            "buy_volume": 6.0,
            "sell_volume": 4.0,
            "volume_delta": 2.0,
            "cvd": 2.0,
            "flow_source": "binance_taker_volume",
        }],
        index=pd.DatetimeIndex(["2026-09-22T03:00:00Z"]),
    )
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        current_price=100.5,
        direction="long",
        entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        risk_reward=3.0,
        flow_source="binance_taker_volume",
        flow_granularity="kline_derived",
        volume_quality="exchange_aggressor",
        tri_core_setup={
            "actionable": True,
            "direction": "long",
            "grade": "A",
            "event_id": "event-chart-1",
        },
    )
    strategy = StrategyResult(
        approved=True,
        direction="long",
        setup_direction="long",
    )
    execution = SimpleNamespace(
        frame=frame,
        signal=signal,
        strategy=strategy,
        metadata=lambda: {
            "snapshot_id": "snap-chart-1",
            "authority": "execution_timeframe_only",
            "decision_authority": "tri_core_setup_engine",
            "timeframe": "15m",
        },
    )

    monkeypatch.setattr(
        chart_api.execution_analyses,
        "get",
        AsyncMock(return_value=execution),
    )
    monkeypatch.setattr(
        chart_api._market,
        "get_ohlcv",
        AsyncMock(return_value=frame),
    )
    projection = {
        "candidate_id": "snap-chart-1:event-chart-1",
        "tri_core": {"status": "ready"},
        "ai_review": {"status": "not_requested"},
        "execution": {"status": "not_run"},
        "risk": {"status": "not_run"},
        "oms": {"status": "not_submitted"},
    }
    mocked_projection = AsyncMock(return_value=projection)
    monkeypatch.setattr(chart_api, "build_authority_status", mocked_projection)

    payload = await chart_api.get_smc_overlay(
        symbol="BTC/USDT",
        timeframe="15m",
        market_type="crypto",
        exchange="binance",
        htf_bias="neutral",
        htf_timeframe=None,
        entry_mode="limit",
        _key="test",
    )

    assert payload["analysis_snapshot"]["snapshot_id"] == "snap-chart-1"
    assert payload["tri_core_setup"]["event_id"] == "event-chart-1"
    assert payload["authority_status"]["candidate_id"] == "snap-chart-1:event-chart-1"
    assert payload["flow_source"] == "binance_taker_volume"
    assert payload["flow_granularity"] == "kline_derived"
    assert payload["volume_quality"] == "exchange_aggressor"

    mocked_projection.assert_awaited_once()
    kwargs = mocked_projection.await_args.kwargs
    assert kwargs["snapshot_id"] == "snap-chart-1"
    assert kwargs["event_id"] == "event-chart-1"


@pytest.mark.anyio
async def test_hrd04_authority_status_exposes_strategy_family_as_metadata():
    view = await build_authority_status(
        symbol="BTC/USDT",
        snapshot_id="snap-family",
        event_id="event-family",
        tri_core_setup={
            "actionable": True,
            "direction": "long",
            "grade": "A",
            "setup_type": "breaker_retest",
            "strategy_family": "swing",
            "entry_policy": "market_eligible",
        },
        structural_rr=2.7,
    )

    assert view["tri_core"]["setup_type"] == "breaker_retest"
    assert view["tri_core"]["strategy_family"] == "swing"
    assert view["tri_core"]["actionable"] is True


@pytest.mark.anyio
async def test_non_actionable_candidate_reports_ai_not_requested_not_unavailable():
    view = await build_authority_status(
        symbol="LINK/USDT",
        snapshot_id="snap-wait",
        event_id="event-wait",
        tri_core_setup={
            "actionable": False,
            "direction": "long",
            "grade": "WAIT",
            "setup_type": "breaker_retest",
        },
        structural_rr=0.55,
    )

    assert view["tri_core"]["status"] == "wait"
    assert view["ai_review"]["status"] == "not_requested"
    assert view["ai_review"]["verdict"] == "NOT_REQUESTED"
    assert view["execution"]["status"] == "not_run"
    assert view["risk"]["status"] == "not_run"


@pytest.mark.anyio
async def test_authority_projection_handles_waiting_retest_intent():
    await decision_trace.record(
        snapshot_id="snap-retest",
        event_id="event-retest",
        symbol="BTC/USDT",
        stage="EXECUTION_REVALIDATION",
        facts={
            "passed": False,
            "reason_code": "MARKET_EXTENSION_EXCEEDED",
            "intent": "wait",
            "reasons": ["Price extended beyond optimal entry zone; wait for retest"],
            "structural_rr": 2.5,
            "executable_rr": 1.4,
        },
    )

    view = await build_authority_status(
        symbol="BTC/USDT",
        snapshot_id="snap-retest",
        event_id="event-retest",
        tri_core_setup={"actionable": True, "direction": "long", "grade": "A"},
        structural_rr=2.5,
    )

    assert view["execution"]["status"] == "waiting_retest"
    assert view["execution"]["intent"] == "wait"
    assert view["order"]["ready"] is False
    assert view["order"]["status"] == "waiting_retest"
    assert view["order"]["manual_override_required"] is False


@pytest.mark.anyio
async def test_authority_projection_handles_limit_armed_intent():
    await decision_trace.record(
        snapshot_id="snap-limit",
        event_id="event-limit",
        symbol="ETH/USDT",
        stage="AI_REVIEW",
        facts={"status": "reviewed", "verdict": "COHERENT", "reason": "No contradiction"},
    )
    await decision_trace.record(
        snapshot_id="snap-limit",
        event_id="event-limit",
        symbol="ETH/USDT",
        stage="EXECUTION_REVALIDATION",
        facts={
            "passed": True,
            "reason_code": "LIMIT_ARMED",
            "intent": "place_limit",
            "auto_armed_limit": True,
            "structural_rr": 3.0,
            "executable_rr": 3.0,
            "order_type": "limit",
        },
    )

    view = await build_authority_status(
        symbol="ETH/USDT",
        snapshot_id="snap-limit",
        event_id="event-limit",
        tri_core_setup={"actionable": True, "direction": "long", "grade": "A"},
        structural_rr=3.0,
    )

    assert view["execution"]["status"] == "limit_armed"
    assert view["execution"]["intent"] == "place_limit"
    assert view["execution"]["auto_armed_limit"] is True
    # Without risk trace, order is NOT ready (P0 fix: no fake risk approval)
    assert view["risk"]["status"] == "not_run"
    assert view["order"]["ready"] is False

    # Once risk evaluates and approves, order becomes ready
    await decision_trace.record(
        snapshot_id="snap-limit",
        event_id="event-limit",
        symbol="ETH/USDT",
        stage="RISK",
        facts={"approved": True, "reason": "Risk checks passed"},
    )
    view_approved = await build_authority_status(
        symbol="ETH/USDT",
        snapshot_id="snap-limit",
        event_id="event-limit",
        tri_core_setup={"actionable": True, "direction": "long", "grade": "A"},
        structural_rr=3.0,
    )
    assert view_approved["order"]["ready"] is True


@pytest.mark.anyio
async def test_authority_projection_handles_closed_session_stock():
    view = await build_authority_status(
        symbol="NVDA",
        snapshot_id="snap-NVDA",
        event_id="wait-NVDA",
        tri_core_setup={
            "actionable": False,
            "direction": "wait",
            "grade": "WAIT",
            "rejection_reasons": ["US regular trading session is closed"],
        },
        structural_rr=None,
    )

    assert view["lifecycle_state"] == "SESSION_CLOSED"
    assert view["lifecycle_reason_code"] == "SESSION_CLOSED"
    assert "US regular trading session is closed" in view["lifecycle_reason"]
    assert view["tri_core"]["status"] == "wait"
    assert view["tri_core"]["actionable"] is False
    assert view["tri_core"]["reasons"] == ["US regular trading session is closed"]
    assert view["execution"]["status"] == "not_run"
    assert view["risk"]["status"] == "not_run"
    assert view["order"]["ready"] is False
    assert view["order"]["status"] == "not_ready"


@pytest.mark.anyio
async def test_authority_projection_handles_waiting_setup():
    view = await build_authority_status(
        symbol="AAPL",
        snapshot_id="snap-AAPL",
        event_id="wait-AAPL",
        tri_core_setup={
            "actionable": False,
            "direction": "wait",
            "grade": "WAIT",
            "rejection_reasons": ["No valid setup pattern identified"],
        },
        structural_rr=None,
    )

    assert view["lifecycle_state"] == "WAIT"
    assert view["lifecycle_reason_code"] == "NO_SETUP"
    assert "No valid setup pattern identified" in view["lifecycle_reason"]
    assert view["tri_core"]["status"] == "wait"
    assert view["tri_core"]["actionable"] is False


@pytest.mark.anyio
async def test_phase1_exposes_single_final_block_reason_for_ai_failure(monkeypatch):
    monkeypatch.setattr(
        "app.services.authority_status.load_runtime_config",
        lambda: {
            "auto_trade_require_ai_approval": True,
            "auto_trade_ai_policy": "strict",
            "auto_trade_accept_caution": False,
        },
    )
    await decision_trace.record(
        snapshot_id="snap-ai-fail",
        event_id="event-ai-fail",
        symbol="BTC/USDT",
        stage="AI_REVIEW",
        facts={
            "status": "error",
            "verdict": "UNAVAILABLE",
            "failure_type": "PROVIDER_UNAVAILABLE",
            "reason": "AI provider or network is unavailable",
            "input_snapshot_id": "snap-ai-fail",
        },
    )

    view = await build_authority_status(
        symbol="BTC/USDT",
        snapshot_id="snap-ai-fail",
        event_id="event-ai-fail",
        tri_core_setup={"actionable": True, "direction": "long", "grade": "A"},
        structural_rr=2.5,
    )

    assert view["final_decision"] == "BLOCKED"
    assert view["final_block_stage"] == "AI_REVIEW"
    assert view["final_block_reason_code"] == "AI_UNAVAILABLE"
    assert "provider" in view["final_block_reason"].lower()
    assert view["decision_summary"] == {
        "final_decision": "BLOCKED",
        "blocking_stage": "AI_REVIEW",
        "reason_code": "AI_UNAVAILABLE",
        "reason": "AI provider or network is unavailable",
    }


@pytest.mark.anyio
async def test_authority_projection_reconciles_active_paper_oms_position(monkeypatch):
    from app.services.paper_oms import paper_oms

    async def mock_list_positions(*args, **kwargs):
        return {
            "total": 1,
            "trades": [
                {
                    "symbol": "DOT/USDT",
                    "status": "open",
                    "order_type": "limit",
                    "direction": "long",
                    "decision_snapshot_id": "snap-manual-1",
                    "tri_core_event_id": "event-manual-1",
                    "entry": 1.1189,
                    "fill_price": 1.1189,
                    "requested_quantity": 1000.0,
                    "filled_quantity": 1000.0,
                    "close_reason": None,
                }
            ],
        }

    monkeypatch.setattr(paper_oms, "list_positions", mock_list_positions)

    view = await build_authority_status(
        symbol="DOT/USDT",
        snapshot_id="snap-manual-1",
        event_id="event-manual-1",
        tri_core_setup={
            "actionable": True,
            "direction": "long",
            "grade": "A",
            "setup_type": "range_boundary_ping_pong",
        },
        structural_rr=2.99,
    )

    assert view["oms"]["status"] == "open"
    assert view["oms"]["fill_price"] == 1.1189
    assert view["order"]["status"] == "open"
    assert view["lifecycle_state"] == "OPEN"


@pytest.mark.anyio
async def test_authority_projection_rejects_opposite_direction_or_mismatched_candidate_oms(monkeypatch):
    from app.services.paper_oms import paper_oms

    async def mock_list_positions(*args, **kwargs):
        return {
            "total": 2,
            "trades": [
                {
                    "symbol": "DOT/USDT",
                    "status": "open",
                    "order_type": "limit",
                    "direction": "short",  # Opposite direction!
                    "decision_snapshot_id": "snap-manual-1",
                    "tri_core_event_id": "event-manual-1",
                    "entry": 1.1189,
                },
                {
                    "symbol": "DOT/USDT",
                    "status": "open",
                    "order_type": "limit",
                    "direction": "long",
                    "decision_snapshot_id": "snap-other-2",  # Mismatched snapshot ID!
                    "tri_core_event_id": "event-other-2",
                    "entry": 1.1189,
                },
            ],
        }

    monkeypatch.setattr(paper_oms, "list_positions", mock_list_positions)

    view = await build_authority_status(
        symbol="DOT/USDT",
        snapshot_id="snap-manual-1",
        event_id="event-manual-1",
        tri_core_setup={
            "actionable": True,
            "direction": "long",
            "grade": "B",
            "setup_type": "range_boundary_ping_pong",
        },
        structural_rr=2.5,
    )

    # Neither position should attach to this candidate card
    assert view["oms"]["status"] == "not_submitted"
    assert view["lifecycle_state"] == "UNKNOWN"


@pytest.mark.anyio
async def test_authority_status_uniform_not_run_representation():
    view = await build_authority_status(
        symbol="ETH/USDT",
        snapshot_id="snap-notrun-1",
        event_id="event-notrun-1",
        tri_core_setup={
            "actionable": True,
            "direction": "long",
            "grade": "B",
            "setup_type": "range_boundary_ping_pong",
        },
        structural_rr=2.0,
    )

    # When execution revalidation and risk have not run, both sub-payloads and stages report NOT_RUN
    assert view["execution"]["status"] == "not_run"
    assert view["execution"]["reason_code"] == "NOT_RUN"
    assert view["stages"]["execution"]["reason_code"] == "NOT_RUN"
    assert "not run" in view["stages"]["execution"]["reason"].lower()

    assert view["risk"]["status"] == "not_run"
    assert view["risk"]["reason_code"] == "NOT_RUN"
    assert view["stages"]["risk"]["reason_code"] == "NOT_RUN"
    assert "not run" in view["stages"]["risk"]["reason"].lower()
    assert view["decision_summary"]["reason_code"] == "NOT_RUN"


