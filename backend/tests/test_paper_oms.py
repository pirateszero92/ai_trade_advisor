"""Phase 6 authoritative Paper OMS regression tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from app.core.json_store import write_json
from app.core.tri_core_contract import TRI_CORE_SETUP_TYPES
from app.models.paper_oms import (
    PaperOMSAccount,
    PaperOMSEvent,
    PaperOMSFill,
    PaperOMSOrder,
    PaperOMSPosition,
    PaperOMSRiskHalt,
)
from app.services.paper_oms import (
    PaperOMS,
    PaperOMSConflict,
    PaperOMSError,
    PaperOMSValidation,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _make_oms(tmp_path: Path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        for table in (
            PaperOMSAccount.__table__,
            PaperOMSPosition.__table__,
            PaperOMSOrder.__table__,
            PaperOMSFill.__table__,
            PaperOMSEvent.__table__,
            PaperOMSRiskHalt.__table__,
        ):
            await connection.run_sync(table.create)
    projection = tmp_path / "paper_trades.json"
    config = tmp_path / "paper_portfolio.json"
    write_json(projection, {})
    write_json(config, {"initial_capital": 100000.0, "currency": "USD"})
    oms = PaperOMS(factory)
    await oms.start(projection, config, subscribe=False)
    return oms, factory, engine, projection, config


def _approved_auto_pilot_payload(*, direction: str, symbol: str) -> dict:
    payload = _order_payload(direction=direction, symbol=symbol)
    snapshot_id = "a1b2c3d4e5f60718293a4b5c"
    event_id = f"sweep:{symbol}:15m:test"
    payload.update({
        "source": "auto_pilot",
        "setup_grade": "A",
        "setup_type": "sweep_reversal",
        "decision_snapshot_id": snapshot_id,
        "decision_trace_id": f"{snapshot_id}:{event_id}",
        "setup_timeframe": "15m",
        "tri_core_event_id": event_id,
        "decision_authority": "tri_core_setup_engine",
        "tri_core_direction": direction,
        "execution_revalidation": {
            "passed": True,
            "snapshot_id": snapshot_id,
            "event_id": event_id,
        },
        "risk_assessment": {
            "approved": True,
            "sizing_authority": "risk_engine",
            "risk_budget_source": "deterministic_config",
            "position_size": 10.0,
            "risk_pct": 1.0,
        },
        "instrument_constraints": {
            "min_quantity": 0.001,
            "min_notional": 1.0,
            "quantity_step": 0.001,
        },
        "ai_review_required": True,
        "ai_review_status": "reviewed",
        "ai_review_verdict": "COHERENT",
    })
    return payload


def _order_payload(*, direction: str, order_type: str = "market", symbol: str) -> dict:
    if direction == "long":
        stop_loss, take_profit = 95.0, 110.0
    else:
        stop_loss, take_profit = 105.0, 90.0
    payload = {
        "symbol": symbol,
        "direction": direction,
        "order_type": order_type,
        "entry": 100.0,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "position_size": 10.0,
        "exchange": "binance",
        "risk_pct": 1.0,
        "idempotency_key": f"entry-{uuid.uuid4()}",
    }
    if order_type == "market":
        from app.engines.price_hub import price_hub

        price_hub.update_price(
            symbol,
            99.995,
            bid=99.99,
            ask=100.0,
            source="test_ws",
            transport="websocket",
            data_quality="test",
        )
    return payload


@pytest.mark.anyio
async def test_market_order_requires_a_fresh_quote(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6NOQUOTE{uuid.uuid4().hex[:6]}/USDT"
    payload = _order_payload(direction="long", order_type="limit", symbol=symbol)
    payload["order_type"] = "market"

    with pytest.raises(PaperOMSError, match="Fresh market quote unavailable"):
        await oms.place_order(payload)

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_auto_pilot_order_requires_approved_risk_assessment(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6NORISK{uuid.uuid4().hex[:6]}/USDT"
    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload["risk_assessment"] = {"approved": False, "position_size": 10.0, "risk_pct": 1.0}

    with pytest.raises(PaperOMSValidation, match="approved RiskAssessment"):
        await oms.place_order(payload)

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_auto_pilot_causal_limit_revalidates_before_fill(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6ARMLIMIT{uuid.uuid4().hex[:6]}/USDT"
    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload.update({
        "order_type": "limit",
        "entry_policy": "limit_retest_only",
        "limit_zone_id": "zone-1",
        "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    })
    from app.engines.price_hub import price_hub
    price_hub.update_price(
        symbol,
        105.0,
        bid=104.99,
        ask=105.0,
        source="test_ws",
        transport="websocket",
        data_quality="test",
    )
    pending = await oms.place_order(payload)
    assert pending["status"] == "pending"

    # Simulate the canonical snapshot expiring while the order waits.
    async with factory() as session:
        position = await session.get(PaperOMSPosition, pending["id"])
        source_payload = dict(position.source_payload or {})
        source_payload["valid_until"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        position.source_payload = source_payload
        await session.commit()

    now_ts = datetime.now(timezone.utc).timestamp()
    changed = await oms.process_market_tick({
        "symbol": symbol,
        "price": 99.0,
        "bid": 98.99,
        "ask": 99.0,
        "last_trade_quantity": 1000.0,
        "sequence": 777,
        "source": "test_ws",
        "received_timestamp": now_ts,
    })

    assert changed[0]["status"] == "cancelled"
    assert "snapshot expired" in changed[0]["close_reason"].lower()
    fills = await oms.list_fills(position_id=pending["id"])
    assert fills["fills"] == []
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_oms_stop_loss_closes_the_matching_candidate_lifecycle(tmp_path):
    from app.services.candidate_lifecycle import (
        CandidateState,
        candidate_lifecycle,
    )

    await candidate_lifecycle.reset()
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6LIFECYCLE{uuid.uuid4().hex[:6]}/USDT"
    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    snapshot_id = payload["decision_snapshot_id"]
    event_id = payload["tri_core_event_id"]
    await candidate_lifecycle.register(
        symbol=symbol,
        snapshot_id=snapshot_id,
        event_id=event_id,
        ready=True,
    )
    for state in (
        CandidateState.AI_REVIEW_PENDING,
        CandidateState.AI_COHERENT,
        CandidateState.EXECUTION_VALIDATING,
        CandidateState.EXECUTION_VALIDATED,
        CandidateState.RISK_APPROVED,
        CandidateState.ORDER_PENDING,
    ):
        assert await candidate_lifecycle.transition(
            symbol=symbol,
            snapshot_id=snapshot_id,
            event_id=event_id,
            new_state=state,
        )

    opened = await oms.place_order(payload)
    lifecycle = await candidate_lifecycle.get(
        snapshot_id=snapshot_id,
        event_id=event_id,
    )
    assert opened["status"] == "open"
    assert lifecycle["state"] == CandidateState.OPEN.value

    now_ts = datetime.now(timezone.utc).timestamp()
    changed = await oms.process_market_tick({
        "symbol": symbol,
        "price": 94.0,
        "bid": 93.99,
        "ask": 94.0,
        "last_trade_quantity": 1000.0,
        "sequence": 778,
        "source": "test_ws",
        "received_timestamp": now_ts,
    })
    lifecycle = await candidate_lifecycle.get(
        snapshot_id=snapshot_id,
        event_id=event_id,
    )
    assert changed[0]["status"] == "closed"
    assert lifecycle["state"] == CandidateState.CLOSED.value
    assert lifecycle["reason_code"] == "OMS_CLOSED"

    await candidate_lifecycle.reset()
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_setup_grade_is_persisted_with_entry_snapshot(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6GRADE{uuid.uuid4().hex[:6]}/USDT"
    payload = _order_payload(direction="long", symbol=symbol)
    payload.update({
        "setup_grade": "S",
        "setup_type": "sweep_reversal",
        "decision_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "setup_timeframe": "1h",
    })

    opened = await oms.place_order(payload)

    assert opened["setup_grade"] == "S"
    assert opened["setup_type"] == "sweep_reversal"
    assert opened["decision_snapshot_id"] == "a1b2c3d4e5f60718293a4b5c"
    assert opened["setup_timeframe"] == "1h"
    assert opened["grade_provenance"] == "entry_snapshot"

    listed = await oms.list_positions(status="open", include_live=False)
    persisted = next(item for item in listed["trades"] if item["id"] == opened["id"])
    assert persisted["setup_grade"] == "S"
    assert persisted["grade_provenance"] == "entry_snapshot"

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("setup_type", sorted(TRI_CORE_SETUP_TYPES))
async def test_every_canonical_tri_core_setup_type_crosses_the_oms_boundary(
    tmp_path, setup_type
):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6TYPE{uuid.uuid4().hex[:6]}/USDT"
    payload = _order_payload(direction="long", symbol=symbol)
    payload.update({
        "setup_grade": "A",
        "setup_type": setup_type,
        "decision_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "setup_timeframe": "15m",
    })

    opened = await oms.place_order(payload)

    assert opened["setup_type"] == setup_type
    assert opened["setup_grade"] == "A"
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_partial_setup_metadata_is_rejected(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6BADGRADE{uuid.uuid4().hex[:6]}/USDT"
    payload = _order_payload(direction="long", symbol=symbol)
    payload["setup_grade"] = "S"

    with pytest.raises(PaperOMSValidation, match="must be supplied together"):
        await oms.place_order(payload)

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_manual_close_without_price_requires_a_fresh_quote(tmp_path):
    from app.engines.price_hub import price_hub

    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6STALE{uuid.uuid4().hex[:6]}/USDT"
    opened = await oms.place_order(_order_payload(direction="long", symbol=symbol))
    price_hub.update_price(
        symbol,
        100.0,
        bid=99.99,
        ask=100.0,
        source="test_ws",
        transport="websocket",
        received_timestamp_ms=1,
    )

    with pytest.raises(PaperOMSError, match="Fresh market quote unavailable"):
        await oms.close_position(opened["id"])

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_protective_gap_uses_observed_market_price(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6GAP{uuid.uuid4().hex[:6]}/USDT"
    opened = await oms.place_order(_order_payload(direction="long", symbol=symbol))

    closed = (await oms.process_market_tick({
        "symbol": symbol,
        "price": 90.0,
        "bid": 90.0,
        "ask": 90.01,
        "sequence": 991,
        "source": "test_ws",
        "transport": "websocket",
        "received_timestamp": 1_787_776_991.0,
    }))[0]
    fills = await oms.list_fills(opened["id"])

    assert closed["status"] == "closed"
    assert fills["fills"][-1]["fill_price"] < 95.0
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_market_tick_matches_canonical_symbol_across_delimiters(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    token = uuid.uuid4().hex[:6].upper()
    stored_symbol = f"P6-{token}/USDT"
    compact_symbol = f"P6{token}USDT"
    placed = await oms.place_order(
        _order_payload(direction="long", order_type="limit", symbol=stored_symbol)
    )

    changed = await oms.process_market_tick({
        "symbol": compact_symbol,
        "price": 99.0,
        "bid": 98.99,
        "ask": 99.0,
        "last_trade_quantity": 1000.0,
        "sequence": 992,
        "source": "test_ws",
        "transport": "websocket",
        "received_timestamp": 1_787_776_992.0,
    })

    assert changed[0]["id"] == placed["id"]
    assert changed[0]["status"] == "open"
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("direction", "close_price"),
    (("long", 110.0), ("short", 90.0)),
)
async def test_market_round_trip_models_costs_and_supports_long_short(
    tmp_path, direction, close_price
):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    opened = await oms.place_order(
        _order_payload(direction=direction, symbol=f"P6{direction.upper()}/USDT")
    )
    assert opened["status"] == "open"
    assert opened["filled_quantity"] == pytest.approx(10.0)
    assert opened["fees_total"] > 0
    assert opened["spread_cost_total"] > 0
    assert opened["slippage_cost_total"] > 0

    closed = await oms.close_position(
        opened["id"],
        close_price=close_price,
        reason="test round trip",
        client_order_id=f"close-{uuid.uuid4()}",
    )
    assert closed["status"] == "closed"
    assert closed["remaining_quantity"] == pytest.approx(0.0)
    assert closed["realized_pnl_gross"] > closed["realized_pnl_net"] > 0
    assert closed["fees_total"] > opened["fees_total"]
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_forex_short_is_sell_to_open_and_buy_to_reduce(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    opened = await oms.place_order(
        _order_payload(direction="short", symbol="XAUUSD")
    )
    await oms.close_position(opened["id"], close_price=90.0, reason="cover short")
    fills = await oms.list_fills(opened["id"])
    assert [fill["side"] for fill in fills["fills"]] == ["sell", "buy"]
    assert [fill["position_effect"] for fill in fills["fills"]] == ["open", "reduce"]
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_limit_partial_fill_cancel_remainder_and_partial_tp(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6PART{uuid.uuid4().hex[:6]}/USDT"
    placed = await oms.place_order(
        _order_payload(direction="long", order_type="limit", symbol=symbol)
    )
    assert placed["status"] == "pending"

    changed = await oms.process_market_tick({
        "symbol": symbol,
        "price": 99.0,
        "bid": 98.99,
        "ask": 99.0,
        "aggressor_side": "sell",
        "last_trade_quantity": 20.0,
        "sequence": 101,
        "source": "test_ws",
        "transport": "websocket",
        "received_timestamp": 1_787_776_000.0,
    })
    assert len(changed) == 1
    partially_open = changed[0]
    assert partially_open["status"] == "open"
    assert partially_open["entry_order_status"] == "partially_filled"
    assert partially_open["filled_quantity"] == pytest.approx(0.2)
    assert partially_open["entry_order_remaining_quantity"] == pytest.approx(9.8)

    after_cancel = await oms.cancel_entry_order(placed["id"])
    assert after_cancel["status"] == "open"
    assert after_cancel["entry_order_status"] == "cancelled"
    assert after_cancel["remaining_quantity"] == pytest.approx(0.2)

    half = await oms.close_position(
        placed["id"],
        close_price=105.0,
        percentage=50.0,
        reason="TP1",
        client_order_id="partial-tp-idempotency",
    )
    assert half["status"] == "open"
    assert half["closed_quantity"] == pytest.approx(0.1)
    assert half["remaining_quantity"] == pytest.approx(0.1)

    same_half = await oms.close_position(
        placed["id"],
        close_price=105.0,
        percentage=50.0,
        reason="duplicate TP1",
        client_order_id="partial-tp-idempotency",
    )
    assert same_half["closed_quantity"] == pytest.approx(0.1)

    final = await oms.close_position(placed["id"], close_price=106.0, reason="runner")
    assert final["status"] == "closed"
    async with factory() as session:
        exit_orders = await session.scalar(
            select(func.count(PaperOMSOrder.id)).where(
                PaperOMSOrder.position_id == placed["id"],
                PaperOMSOrder.position_effect == "reduce",
            )
        )
    assert exit_orders == 2
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_limit_partial_fill_expired_keeps_position_open(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6EXP{uuid.uuid4().hex[:6]}/USDT"
    now_dt = datetime.now(timezone.utc)
    # Set valid_until in the future so placement and first fill pass
    valid_until = (now_dt + timedelta(seconds=60)).isoformat()
    payload = _approved_auto_pilot_payload(
        direction="long",
        symbol=symbol,
    )
    payload["order_type"] = "limit"
    payload["entry"] = 99.0
    payload["valid_until"] = valid_until
    placed = await oms.place_order(payload)
    # First tick: fills 0.5 units
    changed = await oms.process_market_tick({
        "symbol": symbol,
        "price": 99.0,
        "bid": 98.99,
        "ask": 99.0,
        "aggressor_side": "sell",
        "last_trade_quantity": 0.5,
        "sequence": 101,
        "source": "test_ws",
        "transport": "websocket",
        "received_timestamp": datetime.now(timezone.utc).timestamp(),
    })
    assert len(changed) == 1
    assert changed[0]["close_reason"] in (None, ""), f"Rejected with: {changed[0].get('close_reason')}"
    assert changed[0]["status"] == "open"
    assert changed[0]["filled_quantity"] == pytest.approx(0.005)

    # Now simulate expiration of valid_until
    async with factory() as session:
        pos = await session.get(PaperOMSPosition, placed["id"])
        payload = dict(pos.source_payload or {})
        payload["valid_until"] = (now_dt - timedelta(minutes=5)).isoformat()
        pos.source_payload = payload
        await session.commit()

    # Next tick: order is expired, _pending_limit_still_valid should cancel entry order
    # but KEEP position.status == 'open'
    changed2 = await oms.process_market_tick({
        "symbol": symbol,
        "price": 99.0,
        "bid": 98.99,
        "ask": 99.0,
        "aggressor_side": "sell",
        "last_trade_quantity": 1.0,
        "sequence": 102,
        "source": "test_ws",
        "transport": "websocket",
        "received_timestamp": datetime.now(timezone.utc).timestamp(),
    })
    assert len(changed2) == 1
    assert changed2[0]["status"] == "open"  # Must remain open!
    assert changed2[0]["entry_order_status"] == "cancelled"
    assert changed2[0]["remaining_quantity"] == pytest.approx(0.005)

    # Verify the position can still be closed at market
    closed = await oms.close_position(placed["id"], close_price=102.0, reason="TP")
    assert closed["status"] == "closed"
    assert closed["pnl"] > 0
    await oms.stop()
    await engine.dispose()



@pytest.mark.anyio
async def test_cancelled_limit_cannot_fill_and_market_event_is_idempotent(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6RACE{uuid.uuid4().hex[:6]}/USDT"
    placed = await oms.place_order(
        _order_payload(direction="short", order_type="limit", symbol=symbol)
    )
    cancelled = await oms.cancel_entry_order(placed["id"])
    assert cancelled["status"] == "cancelled"
    quote = {
        "symbol": symbol,
        "price": 101.0,
        "bid": 101.0,
        "ask": 101.01,
        "aggressor_side": "buy",
        "last_trade_quantity": 1000.0,
        "sequence": 202,
        "source": "test_ws",
        "received_timestamp": 1_787_776_001.0,
    }
    assert await oms.process_market_tick(quote) == []
    assert await oms.process_market_tick(quote) == []
    async with factory() as session:
        fill_count = await session.scalar(
            select(func.count(PaperOMSFill.id)).where(PaperOMSFill.position_id == placed["id"])
        )
    assert fill_count == 0
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_restart_recovers_open_and_pending_state_from_postgres(tmp_path):
    oms, factory, engine, projection, config = await _make_oms(tmp_path)
    open_trade = await oms.place_order(
        _order_payload(direction="long", symbol=f"P6OPEN{uuid.uuid4().hex[:6]}/USDT")
    )
    pending_trade = await oms.place_order(
        _order_payload(
            direction="short",
            order_type="limit",
            symbol=f"P6PEND{uuid.uuid4().hex[:6]}/USDT",
        )
    )
    await oms.stop()

    recovered_oms = PaperOMS(factory)
    recovery = await recovered_oms.start(projection, config, subscribe=False)
    assert recovery["legacy_imported"] == 0
    assert recovery["open_recovered"] == 1
    assert recovery["pending_recovered"] == 1
    snapshot = await recovered_oms.list_positions(include_live=False)
    statuses = {trade["id"]: trade["status"] for trade in snapshot["trades"]}
    assert statuses[open_trade["id"]] == "open"
    assert statuses[pending_trade["id"]] == "pending"
    assert recovered_oms.health_snapshot()["ready"] is True
    await recovered_oms.stop()
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("direction", ["long", "short"])
async def test_auto_be_and_multi_tier_trailing_advance_on_same_tick_and_persist(
    tmp_path, direction
):
    oms, factory, engine, projection, config = await _make_oms(tmp_path)
    symbol = f"P6PROTECT{direction.upper()}{uuid.uuid4().hex[:4]}/USDT"
    payload = _order_payload(direction=direction, symbol=symbol)
    payload.update({
        "take_profit": 125.0 if direction == "long" else 75.0,
        "auto_be": True,
        "trailing_stop": True,
    })
    opened = await oms.place_order(payload)
    entry = opened["entry"]
    risk = abs(entry - opened["initial_stop_loss"])

    async def tick(r_multiple: float, sequence: int):
        executable = entry + risk * r_multiple if direction == "long" else entry - risk * r_multiple
        return await oms.process_market_tick({
            "symbol": symbol,
            "price": executable,
            "bid": executable if direction == "long" else executable - 0.01,
            "ask": executable + 0.01 if direction == "long" else executable,
            "sequence": sequence,
            "source": "test_ws",
            "transport": "websocket",
            "received_timestamp": 1_787_776_000.0 + sequence,
        })

    be = (await tick(1.01, 301))[0]
    assert be["protection_stage"] == "breakeven"
    assert be["be_triggered"] is True
    assert be["stop_loss"] > entry if direction == "long" else be["stop_loss"] < entry

    tier_15 = (await tick(1.51, 302))[0]
    assert tier_15["protection_stage"] == "trailing_1_5r"
    expected_15 = entry + risk * 0.6 if direction == "long" else entry - risk * 0.6
    assert tier_15["stop_loss"] == pytest.approx(expected_15)

    tier_20 = (await tick(2.01, 303))[0]
    assert tier_20["protection_stage"] == "trailing_2_0r"
    dynamic = (await tick(2.60, 304))[0]
    assert dynamic["protection_stage"] == "trailing_dynamic"
    assert dynamic["max_r_multiple"] >= 2.5

    await oms.stop()
    recovered = PaperOMS(factory)
    recovery = await recovered.start(projection, config, subscribe=False)
    assert recovery["open_recovered"] == 1
    persisted = await recovered.get_position(opened["id"])
    assert persisted["protection_stage"] == "trailing_dynamic"
    assert persisted["stop_loss"] == pytest.approx(dynamic["stop_loss"])
    await recovered.stop()
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("direction", ["long", "short"])
async def test_auto_be_exit_covers_modeled_fee_and_slippage(tmp_path, direction):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6BEEXIT{direction.upper()}{uuid.uuid4().hex[:4]}/USDT"
    payload = _order_payload(direction=direction, symbol=symbol)
    payload.update({
        "take_profit": 125.0 if direction == "long" else 75.0,
        "auto_be": True,
        "trailing_stop": False,
    })
    opened = await oms.place_order(payload)
    entry = opened["entry"]
    risk = abs(entry - opened["initial_stop_loss"])
    favorable = entry + risk * 1.01 if direction == "long" else entry - risk * 1.01
    advanced = (await oms.process_market_tick({
        "symbol": symbol,
        "price": favorable,
        "bid": favorable if direction == "long" else favorable - 0.01,
        "ask": favorable + 0.01 if direction == "long" else favorable,
        "sequence": 401,
        "source": "test_ws",
        "received_timestamp": 1_787_776_401.0,
    }))[0]

    stop = advanced["stop_loss"]
    closed = (await oms.process_market_tick({
        "symbol": symbol,
        "price": stop,
        "bid": stop if direction == "long" else stop - 0.01,
        "ask": stop + 0.01 if direction == "long" else stop,
        "sequence": 402,
        "source": "test_ws",
        "received_timestamp": 1_787_776_402.0,
    }))[0]

    assert closed["status"] == "closed"
    assert "Breakeven Shield" in closed["close_reason"]
    assert closed["realized_pnl_net"] > 0
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("direction", ["long", "short"])
async def test_partial_tp1_and_breakeven_advance(tmp_path, direction):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6TP1{direction.upper()}{uuid.uuid4().hex[:4]}/USDT"
    payload = _order_payload(direction=direction, symbol=symbol)
    tp1_price = 105.0 if direction == "long" else 95.0
    tp2_price = 115.0 if direction == "long" else 85.0
    payload.update({
        "entry": 100.0,
        "stop_loss": 95.0 if direction == "long" else 105.0,
        "take_profit": tp2_price,
        "position_size": 10.0,
        "auto_be": False,
        "source_payload": {
            "take_profit_1": tp1_price,
        },
    })
    opened = await oms.place_order(payload)
    assert opened["status"] == "open"
    assert opened["remaining_quantity"] == 10.0

    # 1. Price reaches TP1 -> partial 50% reduce and stop moved to Breakeven
    reduced = (await oms.process_market_tick({
        "symbol": symbol,
        "price": tp1_price,
        "bid": tp1_price if direction == "long" else tp1_price - 0.01,
        "ask": tp1_price + 0.01 if direction == "long" else tp1_price,
        "sequence": 501,
        "source": "test_ws",
        "received_timestamp": 1_787_776_501.0,
    }))[0]

    assert reduced["status"] == "open"
    assert reduced["remaining_quantity"] == 5.0
    assert reduced["source_payload"].get("tp1_filled") is True
    # Stop loss should be advanced to Breakeven (at or beyond entry covering fee/slippage)
    if direction == "long":
        assert reduced["stop_loss"] > 100.0
    else:
        assert reduced["stop_loss"] < 100.0

    # 2. Price reaches final TP2 -> closes remainder of position
    closed = (await oms.process_market_tick({
        "symbol": symbol,
        "price": tp2_price,
        "bid": tp2_price if direction == "long" else tp2_price - 0.01,
        "ask": tp2_price + 0.01 if direction == "long" else tp2_price,
        "sequence": 502,
        "source": "test_ws",
        "received_timestamp": 1_787_776_502.0,
    }))[0]

    assert closed["status"] == "closed"
    assert closed["remaining_quantity"] == 0.0
    assert "Take Profit" in closed["close_reason"]

    await oms.stop()
    await engine.dispose()



@pytest.mark.anyio
async def test_reset_creates_new_account_generation_without_deleting_audit(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    trade = await oms.place_order(
        _order_payload(direction="short", symbol=f"P6RESET{uuid.uuid4().hex[:6]}/USDT")
    )
    await oms.close_position(trade["id"], close_price=90.0)
    async with factory() as session:
        fills_before = await session.scalar(select(func.count(PaperOMSFill.id)))
    account = await oms.reset_account(
        initial_capital=250000.0,
        currency="USD",
        clear_trades=True,
    )
    assert account["initial_capital"] == 250000.0
    assert account["closed_trades_count"] == 0
    async with factory() as session:
        fills_after = await session.scalar(select(func.count(PaperOMSFill.id)))
        account_count = await session.scalar(select(func.count(PaperOMSAccount.id)))
    assert fills_after == fills_before == 2
    assert account_count == 2
    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_durable_risk_halt_is_reported_but_not_recomputed_as_oms_entry_gate(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    now = datetime.now(timezone.utc)
    async with factory() as session:
        account = (await session.execute(
            select(PaperOMSAccount).where(PaperOMSAccount.active.is_(True))
        )).scalar_one()
        session.add(PaperOMSRiskHalt(
            account_id=account.id,
            reason="Historical risk halt",
            triggered_at=now,
            halted_until=now + timedelta(hours=24),
            source_payload={"test": True},
        ))
        await session.commit()

    snapshot = await oms.account_snapshot()
    assert snapshot["risk_halt"]["active"] is True

    # OMS reports durable risk-halt history but does not independently recompute
    # or enforce portfolio-risk eligibility. Manual/operator execution remains
    # an explicit override; automated flow must arrive with RiskEngine approval.
    opened = await oms.place_order(
        _order_payload(direction="long", symbol=f"P6HALT{uuid.uuid4().hex[:6]}/USDT")
    )
    assert opened["status"] == "open"

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_tick_worker_snapshot_swap_no_dropped_ticks(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    oms.running = True
    oms.ready = True
    from app.services.paper_oms import _norm_symbol
    oms._active_symbols = {_norm_symbol("BTC/USDT"), _norm_symbol("ETH/USDT")}

    # Enqueue first tick
    oms._enqueue_tick({"symbol": "BTC/USDT", "price": 50000.0})
    assert _norm_symbol("BTC/USDT") in oms._latest_ticks
    assert oms._tick_event.is_set()

    # Simulate worker clearing event then swapping snapshot
    oms._tick_event.clear()
    current_ticks = oms._latest_ticks
    oms._latest_ticks = {}

    # Simulate a concurrent incoming tick while processing
    oms._enqueue_tick({"symbol": "ETH/USDT", "price": 3000.0})

    # Verify that the new tick was preserved and tick_event is set
    assert _norm_symbol("ETH/USDT") in oms._latest_ticks
    assert oms._tick_event.is_set()
    assert _norm_symbol("BTC/USDT") in current_ticks

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_trailing_stop_exits_do_not_trip_stop_loss_circuit_breaker(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    for i in range(3):
        trade = await oms.place_order(
            _order_payload(direction="long", symbol=f"TRAIL{i}{uuid.uuid4().hex[:4]}/USDT")
        )
        await oms.close_position(
            trade["id"],
            close_price=99.9,
            reason="Trailing Stop (Profit Protected) 📈",
        )
    # Check that after 3 trailing stop exits, no risk halt is active
    snapshot = await oms.account_snapshot()
    assert snapshot["risk_halt"]["active"] is False

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("direction", ["long", "short"])
async def test_order_auto_assigns_tp1_and_locks_profit_before_breakeven(tmp_path, direction):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6AUTOTP1{direction.upper()}{uuid.uuid4().hex[:4]}/USDT"
    payload = _order_payload(direction=direction, symbol=symbol)
    entry = 100.0
    sl = 95.0 if direction == "long" else 105.0
    tp = 120.0 if direction == "long" else 80.0
    payload.update({
        "entry": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "position_size": 10.0,
        "auto_be": True,
    })
    # Do NOT pass take_profit_1 explicitly — verify it is auto-assigned at 1.0R
    opened = await oms.place_order(payload)
    assert opened["status"] == "open"
    expected_tp1 = 105.0 if direction == "long" else 95.0
    assert opened["take_profit_1"] == pytest.approx(expected_tp1, abs=0.01)
    assert opened["tp1_filled"] is False

    # 1. Price touches auto-assigned TP1 -> 50% partial close occurs
    tick1 = (await oms.process_market_tick({
        "symbol": symbol,
        "price": expected_tp1,
        "bid": expected_tp1 if direction == "long" else expected_tp1 - 0.01,
        "ask": expected_tp1 + 0.01 if direction == "long" else expected_tp1,
        "sequence": 601,
        "source": "test_ws",
        "received_timestamp": 1_787_776_601.0,
    }))[0]

    assert tick1["status"] == "open"
    assert tick1["remaining_quantity"] == pytest.approx(5.0, abs=0.01)
    assert tick1["tp1_filled"] is True
    # Realized profit from TP1 must be locked into cash immediately!
    assert tick1["realized_pnl_net"] > 0
    # Stop loss should be advanced to Breakeven Shield
    assert tick1["protection_stage"] == "breakeven"

    # 2. Market bounces back to Breakeven SL -> Remaining 50% exits with guaranteed positive net profit
    be_sl = tick1["stop_loss"]
    closed = (await oms.process_market_tick({
        "symbol": symbol,
        "price": be_sl,
        "bid": be_sl if direction == "long" else be_sl - 0.01,
        "ask": be_sl + 0.01 if direction == "long" else be_sl,
        "sequence": 602,
        "source": "test_ws",
        "received_timestamp": 1_787_776_602.0,
    }))[0]

    assert closed["status"] == "closed"
    assert "Breakeven Shield" in closed["close_reason"]
    # Total trade Net PnL must be strictly positive (profit in the bank)!
    assert closed["realized_pnl_net"] > 0

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_update_protection_market_price_validation(tmp_path):
    from app.engines.price_hub import price_hub
    from app.services.paper_oms import PaperOMSValidation

    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6PROT{uuid.uuid4().hex[:6]}/USDT"
    payload = _order_payload(direction="long", order_type="limit", symbol=symbol)
    payload["entry"] = 100.0
    payload["stop_loss"] = 90.0
    payload["take_profit"] = 130.0

    created = await oms.place_order(payload)
    pos_id = created["id"]

    # Fill the limit order so status becomes open
    await oms.process_market_tick({
        "symbol": symbol,
        "price": 99.5,
        "sequence": 1,
        "source": "test",
        "received_timestamp": 1000.0,
    })

    # Set price_hub ticker to 110.0
    price_hub.update_price(
        symbol,
        110.0,
        bid=109.9,
        ask=110.1,
        source="test_ws",
        transport="websocket",
        data_quality="test",
    )

    # 1. Setting SL above current market price (110.0) must be rejected
    with pytest.raises(PaperOMSValidation, match="Stop loss .* cannot be set at or above current market price"):
        await oms.update_protection(pos_id, stop_loss=115.0)

    # 2. Setting TP below current market price (110.0) must be rejected
    with pytest.raises(PaperOMSValidation, match="Take profit .* cannot be set at or below current market price"):
        await oms.update_protection(pos_id, take_profit=105.0)

    # 3. Setting valid trailing stop (e.g. 105.0, which is above entry 100.0 but below price 110.0) succeeds!
    updated = await oms.update_protection(pos_id, stop_loss=105.0)
    assert updated["stop_loss"] == 105.0

    await oms.stop()
    await engine.dispose()





@pytest.mark.anyio
async def test_auto_pilot_order_requires_tri_core_authority(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6AUTH{uuid.uuid4().hex[:6]}/USDT"
    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload["decision_authority"] = ""

    with pytest.raises(PaperOMSValidation, match="TriCoreSetupEngine"):
        await oms.place_order(payload)

    payload["decision_authority"] = "tri_core_setup_engine"
    payload["tri_core_direction"] = "short"
    with pytest.raises(PaperOMSValidation, match="must match the deterministic Tri-Core direction"):
        await oms.place_order(payload)

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_auto_pilot_order_requires_passed_execution_revalidation(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P14REVAL{uuid.uuid4().hex[:6]}/USDT"
    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload["execution_revalidation"]["passed"] = False

    with pytest.raises(PaperOMSValidation, match="ExecutionRevalidator"):
        await oms.place_order(payload)

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_auto_pilot_order_requires_matching_trace_and_candidate_identity(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P14TRACE{uuid.uuid4().hex[:6]}/USDT"
    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload["decision_trace_id"] = "wrong-trace"

    with pytest.raises(PaperOMSValidation, match="decision trace identity"):
        await oms.place_order(payload)

    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload["execution_revalidation"]["event_id"] = "different-event"
    with pytest.raises(PaperOMSValidation, match="identity must match"):
        await oms.place_order(payload)

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_auto_pilot_order_requires_coherent_ai_when_ai_is_required(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P14AI{uuid.uuid4().hex[:6]}/USDT"
    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload["ai_review_verdict"] = "CONFLICT"

    with pytest.raises(PaperOMSValidation, match="coherent completed AI review"):
        await oms.place_order(payload)

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_auto_pilot_order_enforces_resolved_instrument_constraints(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P14FILTER{uuid.uuid4().hex[:6]}/USDT"
    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload["instrument_constraints"]["min_quantity"] = 20.0

    with pytest.raises(PaperOMSValidation, match="below instrument minimum"):
        await oms.place_order(payload)

    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload["position_size"] = 10.0005
    payload["size"] = 10.0005
    payload["risk_assessment"]["position_size"] = 10.0005
    with pytest.raises(PaperOMSValidation, match="quantity step"):
        await oms.place_order(payload)

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_oms_preserves_risk_engine_approved_risk_pct_without_reclamping(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P14RISK{uuid.uuid4().hex[:6]}/USDT"
    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload["risk_pct"] = 0.42
    payload["risk_assessment"]["risk_pct"] = 0.42

    opened = await oms.place_order(payload)

    assert opened["risk_pct"] == pytest.approx(0.42)

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_auto_pilot_rejects_non_risk_engine_sizing_authority(tmp_path):
    oms, _factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P17AUTH{uuid.uuid4().hex[:6]}/USDT"
    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload["risk_assessment"]["sizing_authority"] = "llm_reviewer"

    with pytest.raises(PaperOMSValidation, match="sizing authority must be RiskEngine"):
        await oms.place_order(payload)

    payload = _approved_auto_pilot_payload(direction="long", symbol=symbol)
    payload["risk_assessment"]["risk_budget_source"] = "ai_confidence"
    with pytest.raises(PaperOMSValidation, match="deterministic configuration"):
        await oms.place_order(payload)

    await oms.stop()
    await engine.dispose()


@pytest.mark.anyio
async def test_hrd04_strategy_family_is_optional_oms_metadata(tmp_path):
    oms, factory, engine, _projection, _config = await _make_oms(tmp_path)
    symbol = f"P6FAMILY{uuid.uuid4().hex[:6]}/USDT"
    payload = _order_payload(direction="long", symbol=symbol)
    payload.update({
        "setup_grade": "A",
        "setup_type": "breaker_retest",
        "strategy_family": "swing",
        "decision_snapshot_id": "a1b2c3d4e5f60718293a4b5c",
        "setup_timeframe": "15m",
    })

    opened = await oms.place_order(payload)

    async with factory() as session:
        position = await session.get(PaperOMSPosition, opened["id"])
        assert position.source_payload["strategy_family"] == "swing"

    legacy_payload = _order_payload(
        direction="long",
        symbol=f"P6LEGACY{uuid.uuid4().hex[:6]}/USDT",
    )
    legacy = await oms.place_order(legacy_payload)
    assert legacy["status"] in {"open", "pending"}

    await oms.stop()
    await engine.dispose()
