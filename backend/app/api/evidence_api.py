"""Read-only Phase 3 evidence and deterministic replay API."""

from __future__ import annotations

from datetime import datetime
import asyncio
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_api_key
from app.models.base import get_db
from app.models.evidence_event import EvidenceEvent
from app.models.phase3 import BacktestRun
from app.services.evidence import fingerprint, replay_decision_payload, verify_evidence_integrity


router = APIRouter()


class BatchReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    symbol: str | None = Field(default=None, max_length=30)
    timeframe: str | None = Field(default=None, max_length=10)
    source: Literal["manual_analysis", "proactive_scanner"] | None = None
    limit: int = Field(default=100, ge=1, le=500)


def _summary(event: EvidenceEvent, *, include_payload: bool = False) -> dict:
    result = {
        "id": str(event.id),
        "occurred_at": event.occurred_at.isoformat(),
        "recorded_at": event.recorded_at.isoformat(),
        "event_type": event.event_type,
        "source": event.source,
        "symbol": event.symbol,
        "timeframe": event.timeframe,
        "market_type": event.market_type,
        "exchange": event.exchange,
        "mode": event.mode,
        "schema_version": event.schema_version,
        "engine_version": event.engine_version,
        "strategy_version": event.strategy_version,
        "indicator_version": event.indicator_version,
        "regime_version": event.regime_version,
        "replayable": event.replayable,
        "decision_hash": event.decision_hash,
    }
    if include_payload:
        result["payload"] = event.payload
        result["integrity_failures"] = verify_evidence_integrity(event)
    return result


def _run_batch_replay(events: list[EvidenceEvent]) -> tuple[dict, list[dict]]:
    results: list[dict] = []
    matches = 0
    integrity_failures = 0
    replay_errors = 0
    for event in events:
        failures = verify_evidence_integrity(event)
        if failures:
            integrity_failures += 1
            results.append({
                "event_id": str(event.id),
                "status": "integrity_failed",
                "failures": failures,
            })
            continue
        if not event.replayable:
            replay_errors += 1
            results.append({"event_id": str(event.id), "status": "not_replayable"})
            continue
        try:
            replayed = replay_decision_payload(event.payload)
        except Exception as exc:
            replay_errors += 1
            results.append({
                "event_id": str(event.id),
                "status": "replay_error",
                "error": type(exc).__name__,
            })
            continue
        if replayed["match"]:
            matches += 1
        results.append({
            "event_id": str(event.id),
            "symbol": event.symbol,
            "timeframe": event.timeframe,
            "status": "match" if replayed["match"] else "mismatch",
            "expected_decision_hash": replayed["expected_decision_hash"],
            "replayed_decision_hash": replayed["replayed_decision_hash"],
        })
    total = len(events)
    metrics = {
        "events": total,
        "matches": matches,
        "mismatches": total - matches - integrity_failures - replay_errors,
        "integrity_failures": integrity_failures,
        "replay_errors": replay_errors,
        "match_rate": matches / total if total else 0.0,
    }
    return metrics, results


@router.post("/batch-replay")
async def batch_replay_evidence(
    req: BatchReplayRequest,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_db),
):
    query = select(EvidenceEvent)
    conditions = []
    if req.event_ids:
        conditions.append(EvidenceEvent.id.in_(req.event_ids))
    if req.symbol:
        conditions.append(EvidenceEvent.symbol == req.symbol)
    if req.timeframe:
        conditions.append(EvidenceEvent.timeframe == req.timeframe)
    if req.source:
        conditions.append(EvidenceEvent.source == req.source)
    if conditions:
        query = query.where(*conditions)
    query = query.order_by(EvidenceEvent.occurred_at.asc()).limit(req.limit)
    try:
        events = list((await session.scalars(query)).all())
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Evidence store is unavailable") from exc
    if not events:
        raise HTTPException(status_code=404, detail="No evidence events matched the batch")

    metrics, results = await asyncio.to_thread(_run_batch_replay, events)
    strategy_versions = sorted({event.strategy_version for event in events})
    payload = {"metrics": metrics, "results": results}
    run = BacktestRun(
        run_type="batch_replay",
        status="completed" if metrics["replay_errors"] == 0 else "degraded",
        symbol=req.symbol,
        timeframe=req.timeframe,
        strategy_version=strategy_versions[0] if len(strategy_versions) == 1 else "mixed",
        evaluation_mode="deterministic_replay",
        data_start=min(event.occurred_at for event in events),
        data_end=max(event.occurred_at for event in events),
        config=req.model_dump(mode="json"),
        assumptions={"external_market_io": False},
        metrics=metrics,
        result=payload,
        result_hash=fingerprint(payload),
    )
    session.add(run)
    await session.flush()
    return {"run_id": str(run.id), **payload}


@router.get("/events")
async def list_evidence_events(
    symbol: str | None = Query(default=None, max_length=30),
    timeframe: str | None = Query(default=None, max_length=10),
    source: Literal["manual_analysis", "proactive_scanner"] | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100_000),
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_db),
):
    query = select(EvidenceEvent)
    count_query = select(func.count(EvidenceEvent.id))
    conditions = []
    if symbol:
        conditions.append(EvidenceEvent.symbol == symbol)
    if timeframe:
        conditions.append(EvidenceEvent.timeframe == timeframe)
    if source:
        conditions.append(EvidenceEvent.source == source)
    if occurred_from:
        conditions.append(EvidenceEvent.occurred_at >= occurred_from)
    if occurred_to:
        conditions.append(EvidenceEvent.occurred_at <= occurred_to)
    if conditions:
        query = query.where(*conditions)
        count_query = count_query.where(*conditions)
    query = query.order_by(EvidenceEvent.occurred_at.desc(), EvidenceEvent.id.desc())
    query = query.offset(offset).limit(limit)
    try:
        events = list((await session.scalars(query)).all())
        total = int((await session.scalar(count_query)) or 0)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Evidence store is unavailable") from exc
    return {"total": total, "offset": offset, "limit": limit, "events": [_summary(e) for e in events]}


@router.get("/events/{event_id}")
async def get_evidence_event(
    event_id: uuid.UUID,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_db),
):
    try:
        event = await session.get(EvidenceEvent, event_id)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Evidence store is unavailable") from exc
    if event is None:
        raise HTTPException(status_code=404, detail="Evidence event not found")
    return _summary(event, include_payload=True)


@router.post("/events/{event_id}/replay")
async def replay_evidence_event(
    event_id: uuid.UUID,
    _key: str = Depends(verify_api_key),
    session: AsyncSession = Depends(get_db),
):
    try:
        event = await session.get(EvidenceEvent, event_id)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Evidence store is unavailable") from exc
    if event is None:
        raise HTTPException(status_code=404, detail="Evidence event not found")
    failures = verify_evidence_integrity(event)
    if failures:
        raise HTTPException(
            status_code=409,
            detail={"error": "Evidence integrity check failed", "failures": failures},
        )
    if not event.replayable:
        raise HTTPException(status_code=409, detail="Evidence event is not replayable")
    try:
        result = replay_decision_payload(event.payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"event_id": str(event.id), **result}
