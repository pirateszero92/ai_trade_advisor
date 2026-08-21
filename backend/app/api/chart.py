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


@router.get("/ticker")
async def get_ticker(
    symbol: str = Query("BTC/USDT"),
    market_type: Literal["crypto", "forex", "stock"] = Query("crypto"),
    _key: str = Depends(verify_api_key),
):
    """Return live 24h ticker: real-time price, change%, 24h high/low/volume."""
    data = await _market.get_ticker_24h(symbol=symbol, market_type=market_type)
    return data


@router.get("/ohlcv")
async def get_ohlcv(
    symbol: str = Query("BTC/USDT"),
    timeframe: str = Query("1H"),
    market_type: Literal["crypto", "forex", "stock"] = Query("crypto"),
    exchange: str = Query("binance"),
    limit: int = Query(300, ge=10, le=1000),
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

    # 1. Fetch multi-timeframe bias if neutral
    effective_htf_bias = htf_bias
    if effective_htf_bias == "neutral":
        htf_tf = "4h" if timeframe.lower() in ["1m", "3m", "5m", "15m", "30m", "1h"] else "1w"
        try:
            htf_df = await _market.get_ohlcv(symbol, htf_tf, market_type, exchange, limit=80)
            if not htf_df.empty:
                htf_sig = _smc.analyze(htf_df, symbol, htf_tf)
                effective_htf_bias = htf_sig.bias
        except Exception:
            pass

    signal = _smc.analyze(df, symbol, timeframe, effective_htf_bias)

    # Smart directional setup sync with proactive scanner
    direction = "long"
    if signal.liquidity_swept and signal.in_premium:
        direction = "short"  # Sweep of highs in Premium -> Short pullback setup
    elif signal.liquidity_swept and signal.in_discount:
        direction = "long"   # Sweep of lows -> Long bounce
    elif signal.bias == "bearish" or (effective_htf_bias == "bearish" and signal.in_premium):
        direction = "short"
    elif signal.bias == "bullish" or (effective_htf_bias == "bullish" and signal.in_discount):
        direction = "long"
    elif signal.direction in ("long", "short"):
        direction = signal.direction

    entry = float(signal.current_price) if signal.current_price > 0 else float(df["close"].iloc[-1])
    if direction == "long":
        sl = (signal.order_block.bottom * 0.998) if signal.order_block else (entry * 0.992)
        if sl >= entry:
            sl = entry * 0.992
        sl_dist = abs(entry - sl)
        tp = entry + (sl_dist * 2.2)
    else:
        sl = (signal.order_block.top * 1.002) if signal.order_block else (entry * 1.008)
        if sl <= entry:
            sl = entry * 1.008
        sl_dist = abs(entry - sl)
        tp = entry - (sl_dist * 2.2)

    confluence = signal.confluence_score
    if signal.liquidity_swept and signal.in_premium and direction == "short":
        confluence = max(confluence, 80)
    elif signal.liquidity_swept and signal.in_discount and direction == "long":
        confluence = max(confluence, 80)

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
        "htf_bias": signal.htf_bias,
        "confluence": confluence,
        "bos": signal.bos,
        "choch": signal.choch,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "order_block": signal.to_dict()["order_block"],
        "fvg": signal.to_dict()["fvg"],
        "equal_highs": signal.equal_highs,
        "equal_lows": signal.equal_lows,
        "in_discount": signal.in_discount,
        "in_premium": signal.in_premium,
        "equilibrium": signal.equilibrium,
        "current_price": signal.current_price,
        "liquidity_swept": signal.liquidity_swept,
        "sweep_direction": signal.sweep_direction,
        "sweep_price": signal.sweep_price,
        "direction": direction,
        "entry": round(entry, 2),
        "stop_loss": round(sl, 2),
        "take_profit": round(tp, 2),
        "risk_reward": 2.2,
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
