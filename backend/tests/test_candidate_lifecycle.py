import pytest

from app.services.candidate_lifecycle import (
    CandidateLifecycleError,
    CandidateLifecycleService,
    CandidateState,
    TERMINAL_STATES,
)


@pytest.mark.anyio
async def test_candidate_lifecycle_happy_path():
    service = CandidateLifecycleService()
    record = await service.register(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        ready=True,
    )
    assert record.state == CandidateState.TRI_CORE_READY

    for state in (
        CandidateState.AI_REVIEW_PENDING,
        CandidateState.AI_COHERENT,
        CandidateState.EXECUTION_VALIDATING,
        CandidateState.EXECUTION_VALIDATED,
        CandidateState.RISK_APPROVED,
        CandidateState.ORDER_PENDING,
        CandidateState.OPEN,
        CandidateState.CLOSED,
    ):
        assert await service.transition(
            symbol="BTC/USDT",
            snapshot_id="snap-1",
            event_id="event-1",
            new_state=state,
        ) is True

    final = await service.get(snapshot_id="snap-1", event_id="event-1")
    assert final["state"] == "CLOSED"
    assert [h["state"] for h in final["history"]] == [
        "DETECTED",
        "TRI_CORE_READY",
        "AI_REVIEW_PENDING",
        "AI_COHERENT",
        "EXECUTION_VALIDATING",
        "EXECUTION_VALIDATED",
        "RISK_APPROVED",
        "ORDER_PENDING",
        "OPEN",
        "CLOSED",
    ]


@pytest.mark.anyio
async def test_candidate_lifecycle_rejects_backward_transition():
    service = CandidateLifecycleService()
    await service.register(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        ready=True,
    )
    assert await service.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        new_state=CandidateState.EXECUTION_VALIDATING,
    ) is True
    assert await service.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        new_state=CandidateState.TRI_CORE_READY,
    ) is False


@pytest.mark.anyio
async def test_event_replacement_invalidates_old_candidate():
    service = CandidateLifecycleService()
    await service.register(
        symbol="BTC/USDT",
        snapshot_id="snap-old",
        event_id="event-old",
        ready=True,
    )
    await service.register(
        symbol="BTC/USDT",
        snapshot_id="snap-new",
        event_id="event-new",
        ready=True,
    )

    old = await service.get(snapshot_id="snap-old", event_id="event-old")
    current = await service.current("BTC/USDT")
    assert old["state"] == "INVALIDATED"
    assert old["reason_code"] == "EVENT_REPLACED"
    assert current["snapshot_id"] == "snap-new"
    assert current["event_id"] == "event-new"
    assert current["state"] == "TRI_CORE_READY"


@pytest.mark.anyio
async def test_stale_candidate_cannot_reclaim_current_pointer():
    service = CandidateLifecycleService()
    await service.register(
        symbol="BTC/USDT",
        snapshot_id="snap-old",
        event_id="event-old",
        ready=True,
    )
    await service.register(
        symbol="BTC/USDT",
        snapshot_id="snap-new",
        event_id="event-new",
        ready=True,
    )

    with pytest.raises(CandidateLifecycleError, match="stale"):
        await service.register(
            symbol="BTC/USDT",
            snapshot_id="snap-old",
            event_id="event-old",
            ready=True,
            replace_current=False,
        )

    current = await service.current("BTC/USDT")
    assert current["snapshot_id"] == "snap-new"


@pytest.mark.anyio
async def test_stale_ai_response_cannot_mutate_replaced_candidate():
    service = CandidateLifecycleService()
    await service.register(
        symbol="BTC/USDT",
        snapshot_id="snap-old",
        event_id="event-old",
        ready=True,
    )
    await service.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-old",
        event_id="event-old",
        new_state=CandidateState.AI_REVIEW_PENDING,
    )
    await service.register(
        symbol="BTC/USDT",
        snapshot_id="snap-new",
        event_id="event-new",
        ready=True,
    )

    changed = await service.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-old",
        event_id="event-old",
        new_state=CandidateState.AI_COHERENT,
    )
    assert changed is False
    old = await service.get(snapshot_id="snap-old", event_id="event-old")
    assert old["state"] == "INVALIDATED"


@pytest.mark.anyio
async def test_terminal_candidate_cannot_reopen():
    service = CandidateLifecycleService()
    await service.register(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        ready=True,
    )
    assert await service.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        new_state=CandidateState.AI_CONFLICT,
    ) is True
    assert await service.transition(
        symbol="BTC/USDT",
        snapshot_id="snap-1",
        event_id="event-1",
        new_state=CandidateState.EXECUTION_VALIDATING,
    ) is False


@pytest.mark.anyio
async def test_waiting_entry_and_limit_armed_are_non_terminal_and_retestable():
    service = CandidateLifecycleService()
    await service.register(
        symbol="SOL/USDT",
        snapshot_id="snap-wait",
        event_id="event-wait",
        ready=True,
    )
    assert await service.transition(
        symbol="SOL/USDT",
        snapshot_id="snap-wait",
        event_id="event-wait",
        new_state=CandidateState.EXECUTION_VALIDATING,
    ) is True

    # Transition to WAITING_ENTRY when market is temporarily extended
    assert await service.transition(
        symbol="SOL/USDT",
        snapshot_id="snap-wait",
        event_id="event-wait",
        new_state=CandidateState.WAITING_ENTRY,
    ) is True
    rec = await service.get(snapshot_id="snap-wait", event_id="event-wait")
    assert rec["state"] == "WAITING_ENTRY"
    assert CandidateState(rec["state"]) not in TERMINAL_STATES

    # Pullback occurs: transition from WAITING_ENTRY to LIMIT_ARMED
    assert await service.transition(
        symbol="SOL/USDT",
        snapshot_id="snap-wait",
        event_id="event-wait",
        new_state=CandidateState.LIMIT_ARMED,
    ) is True
    rec = await service.get(snapshot_id="snap-wait", event_id="event-wait")
    assert rec["state"] == "LIMIT_ARMED"
    assert CandidateState(rec["state"]) not in TERMINAL_STATES

    # Price triggers: transition from LIMIT_ARMED to ORDER_PENDING
    assert await service.transition(
        symbol="SOL/USDT",
        snapshot_id="snap-wait",
        event_id="event-wait",
        new_state=CandidateState.ORDER_PENDING,
    ) is True
    rec = await service.get(snapshot_id="snap-wait", event_id="event-wait")
    assert rec["state"] == "ORDER_PENDING"


@pytest.mark.anyio
async def test_limit_armed_can_transition_to_risk_rejected():
    service = CandidateLifecycleService()
    await service.register(
        symbol="ADA/USDT",
        snapshot_id="snap-risk",
        event_id="event-risk",
        ready=True,
    )
    await service.transition(
        symbol="ADA/USDT",
        snapshot_id="snap-risk",
        event_id="event-risk",
        new_state=CandidateState.EXECUTION_VALIDATING,
    )
    await service.transition(
        symbol="ADA/USDT",
        snapshot_id="snap-risk",
        event_id="event-risk",
        new_state=CandidateState.LIMIT_ARMED,
    )

    # Risk Engine rejects (e.g. portfolio limit exceeded)
    assert await service.transition(
        symbol="ADA/USDT",
        snapshot_id="snap-risk",
        event_id="event-risk",
        new_state=CandidateState.RISK_REJECTED,
        reason_code="RISK_REJECTED",
        reason="Aggregate portfolio exposure cap exceeded",
    ) is True
    rec = await service.get(snapshot_id="snap-risk", event_id="event-risk")
    assert rec["state"] == "RISK_REJECTED"
    assert rec["reason_code"] == "RISK_REJECTED"


