"""
Opportunities API (Phase 11 Greenfield Delivery)
Exposes persistent opportunities, execution intents, and decision traces to mobile/web clients.
"""

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.security import verify_api_key
from app.engines.market_data import MarketDataEngine
from app.engines.price_hub import price_hub
from app.market_data.quality import DataQuality, DataQualityMetadata
from app.market_data.quote import MarketQuote
from app.orchestrator import orchestrator
from app.services.instrument_rules import instrument_rules

router = APIRouter()
_market = MarketDataEngine()


class ScanOpportunityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(default="SOL/USDT", min_length=1, max_length=30)
    timeframe: Literal["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"] = "15m"
    market_type: Literal["crypto", "forex", "stock"] = "crypto"
    exchange: Literal["binance", "bybit", "innovestx", "mt5", "alpaca", "yfinance"] = "binance"
    account_equity: float = Field(default=10_000.0, gt=0)
    daily_pnl_pct: float = Field(default=0.0, ge=-100, le=1000)


@router.get("/")
async def list_opportunities(
    symbol: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _key: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """Lists persistent opportunities managed across candles."""
    if symbol:
        opps = orchestrator.store.get_active_by_symbol(symbol)
    else:
        opps = orchestrator.store.all()

    if status:
        opps = [o for o in opps if o.state.value.lower() == status.lower()]

    # Sort latest first
    opps.sort(key=lambda o: o.created_at, reverse=True)
    results = [o.model_dump() for o in opps[:limit]]

    return {
        "status": "ok",
        "total": len(results),
        "opportunities": results,
    }


@router.get("/traces/recent")
async def get_recent_traces(
    limit: int = Query(50, ge=1, le=200),
    _key: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """Returns recent append-only decision trace records."""
    traces = [t.model_dump() for t in orchestrator.traces[-limit:]]
    return {
        "status": "ok",
        "total": len(traces),
        "traces": traces,
    }


@router.get("/{opportunity_id}")
async def get_opportunity(
    opportunity_id: str,
    _key: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """Retrieves full details for a single opportunity."""
    opp = orchestrator.store.get(opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail=f"Opportunity {opportunity_id} not found")

    matching_traces = [
        t.model_dump() for t in orchestrator.traces if t.opportunity_id == opportunity_id
    ]

    return {
        "status": "ok",
        "opportunity": opp.model_dump(),
        "traces": matching_traces,
    }


@router.post("/scan")
async def scan_opportunity(
    req: ScanOpportunityRequest,
    _key: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """
    Executes an on-demand Greenfield Decision Orchestration cycle:
    Fetches raw OHLCV + real-time quote -> calculates Facts -> classifies State ->
    detects Setup -> creates/revalidates Opportunity -> plans Execution -> checks Risk -> submits to OMS.
    """
    # 1. Fetch OHLCV
    df = await _market.get_ohlcv(symbol=req.symbol, timeframe=req.timeframe, market_type=req.market_type)
    if df.empty or len(df) < 15:
        raise HTTPException(status_code=502, detail=f"Insufficient market data for {req.symbol}")

    # 2. Fetch Real-time executable quote
    price_info = await price_hub.get_live_price(req.symbol)
    now_dt = datetime.now(timezone.utc)

    if price_info and price_info.get("bid") and price_info.get("ask"):
        quote = MarketQuote(
            symbol=req.symbol,
            bid=float(price_info["bid"]),
            ask=float(price_info["ask"]),
            last=float(price_info.get("price") or price_info["bid"]),
            timestamp=now_dt,
            quality_meta=DataQualityMetadata.create(
                quality=DataQuality.EXCHANGE_NATIVE,
                source="price_hub",
                now=now_dt,
            ),
        )
    else:
        # Fallback to last candle close
        last_price = float(df["close"].iloc[-1])
        spread = last_price * 0.0002
        quote = MarketQuote(
            symbol=req.symbol,
            bid=round(last_price - spread, 6),
            ask=round(last_price + spread, 6),
            last=last_price,
            timestamp=now_dt,
            quality_meta=DataQualityMetadata.create(
                quality=DataQuality.DERIVED,
                source="ohlcv_fallback",
                now=now_dt,
            ),
        )

    # 3. Get Instrument Rules
    try:
        rules = await instrument_rules.get(
            symbol=req.symbol,
            market_type=req.market_type,
            exchange=req.exchange,
        )
    except Exception:
        rules = None

    # 4. Run Greenfield Cycle
    result = orchestrator.run_cycle(
        symbol=req.symbol,
        raw_ohlcv=df,
        quote=quote,
        account_equity=req.account_equity,
        daily_pnl_pct=req.daily_pnl_pct,
        timeframe=req.timeframe,
        market_type=req.market_type,
        exchange=req.exchange,
        rules=rules,
    )

    opp = result.get("opportunity")
    intent = result.get("intent")
    risk = result.get("risk_assessment")
    order = result.get("order")
    ai_rev = result.get("ai_review")

    return {
        "status": result.get("status"),
        "opportunity": opp.model_dump() if opp else None,
        "intent": intent.model_dump() if intent else None,
        "risk_assessment": risk.model_dump() if risk else None,
        "order": order.model_dump() if order else None,
        "ai_review": ai_rev.model_dump() if ai_rev else None,
    }


class ReplayOpportunityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(default="SOL/USDT", min_length=1, max_length=30)
    timeframe: Literal["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"] = "15m"
    market_type: Literal["crypto", "forex", "stock"] = "crypto"
    exchange: Literal["binance", "bybit", "innovestx", "mt5", "alpaca", "yfinance"] = "binance"
    initial_equity: float = Field(default=10_000.0, gt=0)
    warmup_bars: int = Field(default=25, ge=20, le=100)
    limit: int = Field(default=200, ge=30, le=1000)


@router.post("/replay")
async def run_replay(
    req: ReplayOpportunityRequest,
    _key: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """
    Executes a deterministic bar-by-bar historical replay and counterfactual simulation.
    Answers: What happened if Market Entered Immediately vs Waited for Limit Retest vs Skipped?
    """
    from app.replay.engine import ReplayEngine

    df = await _market.get_ohlcv(
        symbol=req.symbol,
        timeframe=req.timeframe,
        limit=req.limit,
        market_type=req.market_type,
        exchange=req.exchange,
    )
    if df is None or df.empty:
        raise HTTPException(status_code=404, detail=f"No OHLCV history found for {req.symbol}")

    if len(df) <= req.warmup_bars:
        raise HTTPException(
            status_code=400,
            detail=f"History has {len(df)} bars, which is insufficient for warmup ({req.warmup_bars} required)",
        )

    engine = ReplayEngine(
        symbol=req.symbol,
        historical_df=df,
        timeframe=req.timeframe,
        initial_equity=req.initial_equity,
        warmup_bars=req.warmup_bars,
    )
    result = engine.run()
    return {
        "status": "ok",
        "result": result.to_dict(),
    }

