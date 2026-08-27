"""Phase 4 real-time market-data observability API."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.core.security import verify_api_key
from app.engines.price_hub import price_hub


router = APIRouter()


@router.get("/status")
async def market_data_status(_key: str = Depends(verify_api_key)) -> dict:
    """Expose provider connectivity, freshness, gaps and fallback quality."""
    return price_hub.health_snapshot()


@router.get("/order-flow")
async def market_order_flow(
    symbol: str = Query(
        "BTC/USDT",
        min_length=1,
        max_length=30,
        pattern=r"^[A-Za-z0-9_./:-]+$",
    ),
    timeframe: Literal["1m", "15m", "1h", "4h", "1d"] = Query("1m"),
    limit: int = Query(100, ge=1, le=500),
    _key: str = Depends(verify_api_key),
) -> dict:
    """Return live aggressor CVD plus exchange-confirmed closed-candle delta."""
    price_hub.register_symbol(symbol)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "live": price_hub.get_order_flow(symbol),
        "closed_candles": price_hub.get_closed_candles(symbol, timeframe, limit=limit),
    }
