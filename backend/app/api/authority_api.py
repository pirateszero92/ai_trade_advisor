"""
Authority API
Unified pipeline decision trace and authority status for trading candidates.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, timedelta
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


@router.get("/funnel")
async def get_funnel_diagnostics(
    since: Optional[str] = Query("24h", description="Lookback window, e.g. '24h', '8h', '1h' or ISO timestamp"),
    symbol: Optional[str] = Query(None, description="Optional symbol filter, e.g. BTC/USDT"),
    market_type: Optional[str] = Query("crypto", description="Optional asset class filter: 'crypto', 'forex', 'stock', or 'all'"),
    _key: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """
    Diagnostic endpoint providing Funnel metrics and rejection breakdowns for candidate setups.
    Tracks conversion from Candle Scan -> Zone -> Armed -> Sweep -> Flow -> Actionable.
    """
    raw_mt = market_type.default if hasattr(market_type, "default") else market_type
    effective_market_type = str(raw_mt).lower() if (raw_mt and str(raw_mt).lower() not in {"none", "query(crypto)"}) else "crypto"

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    if since:
        since_str = since.strip().lower()
        if since_str.endswith("h"):
            try:
                hours = float(since_str[:-1])
                cutoff = now - timedelta(hours=hours)
            except ValueError:
                pass
        elif since_str.endswith("d"):
            try:
                days = float(since_str[:-1])
                cutoff = now - timedelta(days=days)
            except ValueError:
                pass
        else:
            try:
                cutoff = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError:
                pass

    all_records = await decision_trace.list_records(since=cutoff)

    funnel_states: Counter[str] = Counter()
    rejection_codes_counter: Counter[str] = Counter()
    rejection_reasons_counter: Counter[str] = Counter()
    regimes_counter: Counter[str] = Counter()
    symbols_counter: Counter[str] = Counter()
    recent_candidates = []

    for record in all_records:
        rec_sym = record.get("symbol", "")
        if symbol and rec_sym != symbol:
            continue

        created_at_raw = record.get("created_at") or record.get("updated_at")
        if created_at_raw:
            try:
                dt = datetime.fromisoformat(str(created_at_raw).replace("Z", "+00:00"))
                if dt < cutoff:
                    continue
            except ValueError:
                pass

        snapshot_fact = {}
        for ev in record.get("events", []):
            if ev.get("stage") == "SNAPSHOT":
                snapshot_fact = ev.get("facts", {})
                break

        if effective_market_type and effective_market_type != "all":
            rec_market = str(snapshot_fact.get("market_type") or "").lower()
            if not rec_market:
                if "/" in rec_sym or rec_sym.endswith("USDT"):
                    rec_market = "crypto"
                elif rec_sym in {"XAUUSD", "EURUSD", "GBPUSD"}:
                    rec_market = "forex"
                else:
                    rec_market = "stock"
            if rec_market != effective_market_type:
                continue

        trigger_state = snapshot_fact.get("trigger_state", "wait")
        funnel_states[trigger_state] += 1
        symbols_counter[rec_sym] += 1

        regime = snapshot_fact.get("market_regime", "unknown")
        regimes_counter[regime] += 1

        for code in snapshot_fact.get("rejection_codes", []):
            rejection_codes_counter[code] += 1

        for reason in snapshot_fact.get("rejection_reasons", []):
            rejection_reasons_counter[reason] += 1

        if len(recent_candidates) < 30:
            recent_candidates.append({
                "candidate_id": record.get("candidate_id"),
                "symbol": rec_sym,
                "timeframe": snapshot_fact.get("timeframe", "15m"),
                "trigger_state": trigger_state,
                "direction": snapshot_fact.get("direction", "wait"),
                "grade": snapshot_fact.get("grade", "WAIT"),
                "rejection_codes": snapshot_fact.get("rejection_codes", []),
                "rejection_reasons": snapshot_fact.get("rejection_reasons", []),
                "signal_bar_time": snapshot_fact.get("signal_bar_time"),
                "updated_at": record.get("updated_at"),
            })

    total_candidates = sum(funnel_states.values())
    return {
        "status": "ok",
        "since": cutoff.isoformat(),
        "market_type_filter": effective_market_type,
        "total_candidates_analyzed": total_candidates,
        "funnel_stages": dict(funnel_states),
        "rejection_codes": dict(rejection_codes_counter.most_common(20)),
        "rejection_reasons": dict(rejection_reasons_counter.most_common(20)),
        "regime_distribution": dict(regimes_counter),
        "symbols_monitored": dict(symbols_counter),
        "recent_candidates": recent_candidates,
    }
