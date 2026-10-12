from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.engines.smc_engine import SMCSignal
from app.engines.strategy_engine import StrategyResult
from app.services.candidate_lifecycle import CandidateLifecycleService, CandidateState
from app.services.execution_analysis import ExecutionAnalysis, ExecutionAnalysisService
from app.services.execution_revalidator import ExecutionRevalidator


class Rules:
    def price(self, value, *, upward=False):
        return round(float(value), 2)


def _execution_analysis(*, actionable=True, approved=True):
    now = datetime.now(timezone.utc)
    frame = pd.DataFrame(
        {
            "open": [99.0 + i * 0.05 for i in range(20)],
            "high": [100.0 + i * 0.05 for i in range(20)],
            "low": [98.0 + i * 0.05 for i in range(20)],
            "close": [99.5 + i * 0.05 for i in range(20)],
            "volume": [1000.0] * 20,
        },
        index=pd.date_range(end=now, periods=20, freq="15min"),
    )
    signal = SMCSignal(
        symbol="BTC/USDT",
        timeframe="15m",
        direction="long",
        entry=100.0,
        stop_loss=98.0,
        take_profit=106.0,
        risk_reward=3.0,
        tri_core_setup={
            "actionable": actionable,
            "direction": "long" if actionable else "wait",
            "event_id": "event-1" if actionable else "",
            "entry_policy": "market_eligible" if actionable else "none",
            "order_type": "market" if actionable else "none",
            "risk_reward": 3.0 if actionable else 0.0,
        },
    )
    strategy = StrategyResult(
        approved=approved,
        direction="long" if approved else "wait",
        setup_direction="long" if approved else "wait",
    )
    return ExecutionAnalysis(
        symbol="BTC/USDT",
        timeframe="15m",
        frame=frame,
        signal=signal,
        strategy=strategy,
        config_snapshot={
            "tri_core_policy": {
                "market_entry_max_extension_atr": 0.25,
                "no_chase_max_extension_atr": 0.60,
            }
        },
        snapshot_id="snap-1",
        generated_at=now,
        valid_until=now + timedelta(minutes=10),
    )
def _quote(*, bid=100.0, ask=100.02, age=0.0):
    ts = datetime.now(timezone.utc).timestamp() - age
    return {
        "bid": bid,
        "ask": ask,
        "timestamp": ts,
        "received_timestamp": ts,
    }


def test_phase18_tri_core_fail_never_requests_ai():
    analysis = _execution_analysis(actionable=False, approved=False)

    should_review, reason = ExecutionAnalysisService._should_trigger_ai_review(analysis)

    assert should_review is False
    assert "AI was not requested" in reason


def test_phase18_only_actionable_approved_tri_core_candidate_requests_ai():
    analysis = _execution_analysis(actionable=True, approved=True)

    should_review, reason = ExecutionAnalysisService._should_trigger_ai_review(analysis)

    assert should_review is True
    assert reason == "deterministic_tri_core_setup"


@pytest.mark.parametrize(
    ("quote_factory", "minimum_rr", "event_id", "expected"),
    [
        (lambda: _quote(age=30), 2.0, "event-1", "QUOTE_STALE"),
        (lambda: _quote(bid=99.0, ask=101.0), 2.0, "event-1", "SPREAD_TOO_WIDE"),
        (lambda: _quote(bid=104.9, ask=105.0), 1.0, "event-1", "NO_CHASE"),
        (lambda: _quote(), 5.0, "event-1", "EXECUTABLE_RR_TOO_LOW"),
        (lambda: _quote(), 2.0, "old-event", "EVENT_REPLACED"),
    ],
)
def test_phase18_execution_revalidator_fail_closed_matrix(
    quote_factory, minimum_rr, event_id, expected
):
    analysis = _execution_analysis()

    result = ExecutionRevalidator().validate(
        analysis=analysis,
        decision_snapshot_id="snap-1",
        tri_core_event_id=event_id,
        quote=quote_factory(),
        direction="long",
        minimum_executable_rr=minimum_rr,
        instrument_rules=Rules(),
        fee_bps=1.0,
        slippage_bps=1.0,
    )

    assert result.passed is False
    assert result.reason_code == expected


def test_phase18_execution_geometry_invalid_blocks_before_risk():
    analysis = _execution_analysis()
    analysis.signal.stop_loss = 101.0

    result = ExecutionRevalidator().validate(
        analysis=analysis,
        decision_snapshot_id="snap-1",
        tri_core_event_id="event-1",
        quote=_quote(),
        direction="long",
        minimum_executable_rr=2.0,
        instrument_rules=Rules(),
    )

    assert result.passed is False
    assert result.reason_code == "GEOMETRY_INVALID"


@pytest.mark.anyio
async def test_phase18_replaced_candidate_cannot_resume_after_stale_ai():
    lifecycle = CandidateLifecycleService()
    await lifecycle.register(
        symbol="BTC/USDT",
        snapshot_id="old",
        event_id="old-event",
        ready=True,
    )
    await lifecycle.transition(
        symbol="BTC/USDT",
        snapshot_id="old",
        event_id="old-event",
        new_state=CandidateState.AI_REVIEW_PENDING,
    )
    await lifecycle.register(
        symbol="BTC/USDT",
        snapshot_id="new",
        event_id="new-event",
        ready=True,
    )

    changed = await lifecycle.transition(
        symbol="BTC/USDT",
        snapshot_id="old",
        event_id="old-event",
        new_state=CandidateState.AI_COHERENT,
    )

    assert changed is False
    old = await lifecycle.get(snapshot_id="old", event_id="old-event")
    current = await lifecycle.current("BTC/USDT")
    assert old["state"] == "INVALIDATED"
    assert current["snapshot_id"] == "new"
