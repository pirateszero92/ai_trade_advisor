"""Canonical paper-trading API namespace.

Legacy ``/api/v1/trades`` routes remain temporarily for older clients, but all
new clients should use this namespace. Every wrapper pins mode to ``paper``.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends

from app.api import trades
from app.core.security import verify_api_key
from app.services.paper_oms import PaperOMSError, paper_oms


router = APIRouter()


@router.post("/orders")
async def place_paper_order(
    req: trades.PlaceOrderRequest,
    api_key: str = Depends(verify_api_key),
):
    return await trades.place_order(req=req, _key=api_key)


@router.get("/orders")
async def list_paper_orders(
    status: Optional[Literal["open", "pending", "closed", "cancelled"]] = None,
    broker: Optional[Literal["paper", "all"]] = None,
    api_key: str = Depends(verify_api_key),
):
    return await trades.list_trades(
        status=status,
        mode="paper",
        broker=broker,
        _key=api_key,
        live_session_token=None,
    )


@router.get("/orders/{trade_id}")
async def get_paper_order(
    trade_id: str,
    api_key: str = Depends(verify_api_key),
):
    return await trades.get_trade(
        trade_id=trade_id,
        _key=api_key,
        live_session_token=None,
    )


@router.get("/orders/{trade_id}/fills")
async def list_paper_order_fills(
    trade_id: str,
    _api_key: str = Depends(verify_api_key),
):
    if not trades._paper_oms_available():
        return {"total": 0, "fills": [], "authority": "legacy_json"}
    try:
        return await paper_oms.list_fills(trade_id)
    except PaperOMSError as exc:
        trades._raise_paper_oms_http(exc)


@router.get("/oms/status")
async def get_paper_oms_status(_api_key: str = Depends(verify_api_key)):
    return paper_oms.health_snapshot()


@router.post("/orders/{trade_id}/close")
async def close_paper_order(
    trade_id: str,
    req: trades.CloseTradeRequest,
    api_key: str = Depends(verify_api_key),
):
    return await trades.close_trade(trade_id=trade_id, req=req, _key=api_key)


@router.delete("/orders/{trade_id}")
async def cancel_paper_order(
    trade_id: str,
    api_key: str = Depends(verify_api_key),
):
    return await trades.cancel_trade(trade_id=trade_id, _key=api_key)


@router.get("/account")
async def get_paper_account(api_key: str = Depends(verify_api_key)):
    return await trades.get_account_portfolio(
        mode="paper",
        broker="paper",
        _key=api_key,
        live_session_token=None,
    )


@router.post("/account/reset")
async def reset_paper_account(
    req: trades.ResetAccountRequest,
    api_key: str = Depends(verify_api_key),
):
    return await trades.reset_paper_account(req=req, _key=api_key)


@router.get("/evaluation")
async def get_paper_evaluation(
    api_key: str = Depends(verify_api_key),
):
    from app.services.paper_evaluation import evaluate_live_paper
    return await evaluate_live_paper()


@router.get("/counterfactual/replay")
async def get_counterfactual_replay_summary(
    api_key: str = Depends(verify_api_key),
):
    from app.services.counterfactual_replay import CounterfactualReplayEngine
    engine = CounterfactualReplayEngine()
    cache = engine.load_cache()
    results = list(cache.values())
    summary = CounterfactualReplayEngine.summarize_replay(results)
    return {
        "status": "ready",
        "cached_replays_count": len(results),
        "summary": summary,
        "sample": results[:20],
    }


@router.post("/counterfactual/replay")
async def run_counterfactual_replay(
    limit: int = 100,
    force_recompute: bool = False,
    api_key: str = Depends(verify_api_key),
):
    from app.engines.market_data import MarketDataEngine
    from app.services.counterfactual_replay import CounterfactualReplayEngine
    from app.services.decision_trace import decision_trace
    from app.services.paper_evaluation import extract_counterfactual_filtered_candidates

    records = await decision_trace.list_records()
    filtered = extract_counterfactual_filtered_candidates(records)
    if limit > 0:
        filtered = filtered[:limit]

    market = MarketDataEngine()
    engine = CounterfactualReplayEngine()
    results = await engine.replay_candidates_batch(
        filtered,
        market_data_engine=market,
        force_recompute=force_recompute,
    )
    summary = CounterfactualReplayEngine.summarize_replay(results)
    return {
        "status": "completed",
        "candidates_processed": len(results),
        "summary": summary,
    }


