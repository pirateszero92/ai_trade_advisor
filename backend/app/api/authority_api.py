"""
Authority API
Unified pipeline decision trace and authority status for trading candidates.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import verify_api_key
from app.services.authority_status import get_authority_status_for_symbol, build_authority_status
from app.services.decision_trace import decision_trace

router = APIRouter()


@router.get("/status")
async def get_authority_status(
    symbol: str = Query(..., description="Trading pair symbol (e.g. BTC/USDT)"),
    exchange: Optional[str] = Query(None, description="Optional exchange name"),
    timeframe: Optional[str] = Query(None, description="Optional timeframe"),
    _key: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """
    Get unified pipeline authority status, decision gates, and stage breakdown for a symbol.
    
    Returns structured results for:
    - tri_core (setup generation)
    - systemic_risk (BTC / macro volatility)
    - ai_review (Ollama Cloud / local LLM verdict)
    - execution (revalidation, spread, R:R)
    - risk (portfolio risk, daily halt, cluster cooldown)
    - oms (order submission and fill state)
    - final_decision (READY, BLOCKED, WAIT, SUBMITTED)
    """
    status = await get_authority_status_for_symbol(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
    )
    return {
        "status": "ok",
        "symbol": symbol,
        "authority_status": status,
    }


@router.get("/trace/{candidate_id:path}")
async def get_candidate_trace(
    candidate_id: str,
    _key: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """Get raw chronological audit trace events for a given candidate_id (snapshot_id:event_id)."""
    parts = candidate_id.split(":", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=400,
            detail="candidate_id must be in the format {snapshot_id}:{event_id}",
        )
    snapshot_id, event_id = parts
    record = await decision_trace.get(snapshot_id=snapshot_id, event_id=event_id)
    if not record:
        raise HTTPException(status_code=404, detail="Decision trace not found")
    return {
        "status": "ok",
        "candidate_id": candidate_id,
        "trace": record,
    }
