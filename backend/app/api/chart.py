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
from app.engines.strategy_engine import StrategyEngine
from app.services.analysis_snapshot import analysis_snapshots
from app.services.mtf_analysis import mtf_analyses

router = APIRouter()
_market = MarketDataEngine()
_smc = SMCEngine()
_strategy = StrategyEngine()

Timeframe = Literal["1m", "2m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"]
Exchange = Literal["binance", "bybit", "innovestx", "mt5", "alpaca", "yfinance"]


@router.get("/ticker")
async def get_ticker(
    symbol: str = Query("BTC/USDT", min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$"),
    market_type: Literal["crypto", "forex", "stock"] = Query("crypto"),
    _key: str = Depends(verify_api_key),
):
    """Return live 24h ticker: real-time price, change%, 24h high/low/volume."""
    data = await _market.get_ticker_24h(symbol=symbol, market_type=market_type)
    return data


@router.get("/ohlcv")
async def get_ohlcv(
    symbol: str = Query("BTC/USDT"),
    timeframe: Timeframe = Query("1h"),
    market_type: Literal["crypto", "forex", "stock"] = Query("crypto"),
    exchange: Exchange = Query("binance"),
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

    candles = []
    for idx, row in df.iterrows():
        candle = {
            "t": idx.isoformat(),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
            "v": float(row["volume"]),
        }
        for field in ("buy_volume", "sell_volume", "volume_delta", "cvd"):
            if field in row and row[field] is not None:
                candle[field] = float(row[field])
        if "flow_source" in row and isinstance(row["flow_source"], str):
            candle["flow_source"] = row["flow_source"]
        candles.append(candle)
    return {"symbol": symbol, "timeframe": timeframe, "candles": candles}


@router.get("/overlay")
async def get_smc_overlay(
    symbol: str = Query("BTC/USDT", min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$"),
    timeframe: Timeframe = Query("1h"),
    market_type: Literal["crypto", "forex", "stock"] = Query("crypto"),
    exchange: Exchange = Query("binance"),
    htf_bias: Literal["bullish", "bearish", "neutral"] = Query("neutral"),
    htf_timeframe: Timeframe | None = Query(None),
    entry_mode: Literal["limit", "market"] | None = Query(None),
    _key: str = Depends(verify_api_key),
):
    """
    Return SMC overlay data: swing points, OBs, FVGs, equal levels,
    and premium/discount zones — all in chart-ready format.
    """
    try:
        snapshot = await analysis_snapshots.get(
            symbol=symbol,
            timeframe=timeframe,
            htf_timeframe=htf_timeframe,
            market_type=market_type,
            exchange=exchange,
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # The runtime entry mode is shared with Scanner unless an older client
    # explicitly supplies one for backward compatibility.
    if entry_mode is None:
        from app.services.event_trigger import _get_cached_runtime_settings

        effective_entry_mode = _get_cached_runtime_settings().get("entry_mode", "limit")
    else:
        effective_entry_mode = entry_mode
    effective_htf_bias = snapshot.htf_bias if htf_bias == "neutral" else htf_bias
    overlay_signal = _smc.analyze(
        snapshot.ltf.copy(), symbol, timeframe, effective_htf_bias,
        entry_mode=effective_entry_mode,
    )
    try:
        mtf = await mtf_analyses.get(
            symbol=symbol,
            market_type=market_type,
            exchange=exchange,
            entry_mode=effective_entry_mode,
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"MTF analysis unavailable: {exc}") from exc
    execution_signal = mtf.trigger_signal
    strategy = mtf.strategy
    direction = strategy.direction if mtf.actionable else "wait"
    entry = float(execution_signal.entry or execution_signal.current_price or 0.0)
    sl = float(execution_signal.stop_loss or 0.0)
    tp = float(execution_signal.take_profit or 0.0)

    swing_highs = [
        {"t": sp.timestamp.isoformat(), "price": sp.price}
        for sp in overlay_signal.swing_highs
    ]
    swing_lows = [
        {"t": sp.timestamp.isoformat(), "price": sp.price}
        for sp in overlay_signal.swing_lows
    ]

    candles = []
    for idx, row in snapshot.ltf.iterrows():
        candle = {
            "t": idx.isoformat(),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
            "v": float(row["volume"]),
        }
        for field in ("buy_volume", "sell_volume", "volume_delta", "cvd"):
            if field in row and row[field] is not None:
                candle[field] = float(row[field])
        if "flow_source" in row and isinstance(row["flow_source"], str):
            candle["flow_source"] = row["flow_source"]
        candles.append(candle)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "htf_timeframe": snapshot.htf_timeframe,
        "analysis_snapshot": snapshot.metadata(),
        "mtf_analysis_snapshot": mtf.metadata(),
        "mtf": mtf.to_dict(),
        "candles": candles,
        "bias": overlay_signal.bias,
        "htf_bias": mtf.stages["bias"].signal.bias,
        "confluence": execution_signal.confluence_score,
        "indicator_decision": execution_signal.indicator_decision,
        "market_regime": execution_signal.market_regime,
        "overlay_indicator_decision": overlay_signal.indicator_decision,
        "overlay_market_regime": overlay_signal.market_regime,
        "bos": overlay_signal.bos,
        "choch": overlay_signal.choch,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "order_block": overlay_signal.to_dict()["order_block"],
        "fvg": overlay_signal.to_dict()["fvg"],
        "equal_highs": overlay_signal.equal_highs,
        "equal_lows": overlay_signal.equal_lows,
        "in_discount": overlay_signal.in_discount,
        "in_premium": overlay_signal.in_premium,
        "equilibrium": overlay_signal.equilibrium,
        "current_price": execution_signal.current_price,
        "liquidity_swept": overlay_signal.liquidity_swept,
        "sweep_direction": overlay_signal.sweep_direction,
        "sweep_price": overlay_signal.sweep_price,
        "direction": direction,
        "setup_direction": strategy.setup_direction,
        "strategy": strategy.to_dict(),
        "actionable": mtf.actionable,
        "execution_timeframe": mtf.stages["trigger"].timeframe,
        "entry": round(entry, 2),
        "stop_loss": round(sl, 2),
        "take_profit": round(tp, 2),
        "risk_reward": execution_signal.risk_reward,
    }


@router.get("/quote")
async def get_quote(
    symbol: str = Query("BTC/USDT", min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$"),
    market_type: Literal["crypto", "forex", "stock"] = Query("crypto"),
    exchange: Exchange = Query("binance"),
    _key: str = Depends(verify_api_key),
):
    """Return fast live price quote for realtime ticker using 24h market stats."""
    ticker_data = await _market.get_ticker_24h(symbol=symbol, market_type=market_type)
    if ticker_data and ticker_data.get("price", 0) > 0:
        import datetime
        return {
            "symbol": symbol,
            "price": ticker_data.get("price", 0.0),
            "change_24h": round(ticker_data.get("change_24h", 0.0), 2),
            "high": ticker_data.get("high_24h", 0.0),
            "low": ticker_data.get("low_24h", 0.0),
            "volume": ticker_data.get("volume_24h", 0.0),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

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
        "change_24h": None,
        "change_window_pct": round(change_pct, 2),
        "high": float(df["high"].max()),
        "low": float(df["low"].min()),
        "volume": float(df["volume"].sum()),
        "timestamp": df.index[-1].isoformat(),
    }
