"""
Chart API
Returns OHLCV candle data and SMC overlay data for the mobile chart widget.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import verify_api_key
from app.engines.market_data import MarketDataEngine
from app.engines.smc_engine import SMCEngine

router = APIRouter()
_market = MarketDataEngine()
_smc = SMCEngine()


@router.get("/ohlcv")
async def get_ohlcv(
    symbol: str = Query("BTC/USDT"),
    timeframe: str = Query("1H"),
    market_type: Literal["crypto", "forex", "stock"] = Query("crypto"),
    exchange: str = Query("binance"),
    limit: int = Query(300, le=1000),
    _key: str = Depends(verify_api_key),
):
    """Return OHLCV candles as a list of dicts ready for charting."""
    df = await _market.get_ohlcv(
        symbol=symbol,
        timeframe=timeframe,
        market_type=market_type,
        exchange=exchange,
        limit=limit,
    )
    if df.empty:
        raise HTTPException(status_code=502, detail="No market data available")

    candles = [
        {
            "t": idx.isoformat(),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
            "v": float(row["volume"]),
        }
        for idx, row in df.iterrows()
    ]
    return {"symbol": symbol, "timeframe": timeframe, "candles": candles}


@router.get("/overlay")
async def get_smc_overlay(
    symbol: str = Query("BTC/USDT"),
    timeframe: str = Query("1H"),
    market_type: Literal["crypto", "forex", "stock"] = Query("crypto"),
    exchange: str = Query("binance"),
    htf_bias: Literal["bullish", "bearish", "neutral"] = Query("neutral"),
    limit: int = Query(300, le=1000),
    _key: str = Depends(verify_api_key),
):
    """
    Return SMC overlay data: swing points, OBs, FVGs, equal levels,
    and premium/discount zones — all in chart-ready format.
    """
    df = await _market.get_ohlcv(
        symbol=symbol,
        timeframe=timeframe,
        market_type=market_type,
        exchange=exchange,
        limit=limit,
    )
    if df.empty:
        raise HTTPException(status_code=502, detail="No market data available")

    signal = _smc.analyze(df, symbol, timeframe, htf_bias)

    swing_highs = [
        {"t": sp.timestamp.isoformat(), "price": sp.price}
        for sp in signal.swing_highs
    ]
    swing_lows = [
        {"t": sp.timestamp.isoformat(), "price": sp.price}
        for sp in signal.swing_lows
    ]

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bias": signal.bias,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "order_block": signal.to_dict()["order_block"],
        "fvg": signal.to_dict()["fvg"],
        "equal_highs": signal.equal_highs,
        "equal_lows": signal.equal_lows,
        "equilibrium": signal.equilibrium,
        "current_price": signal.current_price,
        "liquidity_swept": signal.liquidity_swept,
        "sweep_direction": signal.sweep_direction,
        "sweep_price": signal.sweep_price,
    }


@router.get("/quote")
async def get_quote(
    symbol: str = Query("BTC/USDT"),
    market_type: Literal["crypto", "forex", "stock"] = Query("crypto"),
    exchange: str = Query("binance"),
    _key: str = Depends(verify_api_key),
):
    """Return fast live price quote for realtime ticker."""
    df = await _market.get_ohlcv(
        symbol=symbol,
        timeframe="1m",
        market_type=market_type,
        exchange=exchange,
        limit=5,
    )
    if df.empty:
        raise HTTPException(status_code=502, detail="No market data available")

    last_row = df.iloc[-1]
    first_row = df.iloc[0]
    last_price = float(last_row["close"])
    open_price = float(first_row["open"])
    change_pct = ((last_price - open_price) / open_price) * 100 if open_price > 0 else 0.0

    return {
        "symbol": symbol,
        "price": last_price,
        "change_24h": round(change_pct, 2),
        "high": float(df["high"].max()),
        "low": float(df["low"].min()),
        "volume": float(df["volume"].sum()),
        "timestamp": df.index[-1].isoformat(),
    }
