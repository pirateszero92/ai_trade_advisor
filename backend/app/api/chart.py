"""
Chart API
Returns OHLCV candle data and SMC overlay data for the mobile chart widget.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import verify_api_key
from app.engines.market_data import MarketDataEngine
from app.engines.smc_engine import SMCEngine
from app.engines.timeframe_profiles import (
    load_timeframe_profiles,
    validate_timeframe_profiles,
)
from app.services.execution_analysis import execution_analyses

router = APIRouter()
_market = MarketDataEngine()
_smc = SMCEngine()

Timeframe = Literal["1m", "2m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"]
Exchange = Literal["binance", "bybit", "innovestx", "mt5", "alpaca", "yfinance"]


def _serialize_candle(idx, row) -> dict:
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
    return candle


def _approved_execution_levels(signal: Any, approved: bool) -> dict[str, float | None]:
    """Expose order geometry only when the canonical Strategy Gate approves."""
    if not approved:
        return {"entry": None, "stop_loss": None, "take_profit": None, "risk_reward": 0.0}
    return {
        "entry": round(float(signal.entry or signal.current_price or 0.0), 4),
        "stop_loss": round(float(signal.stop_loss or 0.0), 4),
        "take_profit": round(float(signal.take_profit or 0.0), 4),
        "risk_reward": float(signal.risk_reward or 0.0),
    }


def _clean_smc_overlay(signal: Any) -> dict[str, Any]:
    """Build a rich chart overlay matching TradingView / LuxAlgo SMC v5.

    Provides unmitigated Order Blocks, Swing and Internal BOS/CHoCH structures,
    FVGs, and Equal High/Low liquidity zones for authentic institutional charting.
    """
    payload = signal.to_dict()

    # 1. Unmitigated Order Blocks (both Swing and Internal)
    raw_obs = payload.get("order_blocks") or []
    unmitigated_obs = [
        ob for ob in raw_obs
        if isinstance(ob, dict) and not ob.get("mitigated", False)
    ]
    if not unmitigated_obs:
        primary_ob = payload.get("order_block")
        if isinstance(primary_ob, dict) and not primary_ob.get("mitigated", False):
            unmitigated_obs.append(primary_ob)
    # Sort by origin/confirmation index (most recent first) and keep up to 15 active zones
    unmitigated_obs.sort(
        key=lambda item: int(item.get("origin_index", item.get("index", 0))),
        reverse=True,
    )
    payload["order_blocks"] = unmitigated_obs[:15]

    # 2. Unmitigated Fair Value Gaps
    raw_fvgs = payload.get("fvgs") or []
    unmitigated_fvgs = [
        fvg for fvg in raw_fvgs
        if isinstance(fvg, dict) and not fvg.get("mitigated", False)
    ]
    if not unmitigated_fvgs:
        primary_fvg = payload.get("fvg")
        if isinstance(primary_fvg, dict) and not primary_fvg.get("mitigated", False):
            unmitigated_fvgs.append(primary_fvg)
    unmitigated_fvgs.sort(
        key=lambda item: int(item.get("origin_index", item.get("index", 0))),
        reverse=True,
    )
    payload["fvgs"] = unmitigated_fvgs[:15]

    # 2b. Unmitigated Breaker Blocks (S/R Flips)
    raw_breakers = payload.get("breaker_blocks") or []
    unmitigated_breakers = [
        b for b in raw_breakers
        if isinstance(b, dict) and not b.get("mitigated", False)
    ]
    unmitigated_breakers.sort(
        key=lambda item: int(item.get("confirmed_index", item.get("index", 0))),
        reverse=True,
    )
    payload["breaker_blocks"] = unmitigated_breakers[:15]

    # 3. Market Structure Breaks: Keep recent history for both Swing and Internal
    swing = [s for s in (payload.get("swing_structures") or []) if isinstance(s, dict)]
    internal = [s for s in (payload.get("internal_structures") or []) if isinstance(s, dict)]

    swing.sort(key=lambda item: int(item.get("break_index", -1)), reverse=True)
    internal.sort(key=lambda item: int(item.get("break_index", -1)), reverse=True)

    payload["swing_structures"] = swing[:20]
    payload["internal_structures"] = internal[:25]

    # 4. Equal Highs and Equal Lows
    payload["equal_highs"] = list(payload.get("equal_highs") or [])[-10:]
    payload["equal_lows"] = list(payload.get("equal_lows") or [])[-10:]
    payload["equal_high_levels"] = list(payload.get("equal_high_levels") or [])[-10:]
    payload["equal_low_levels"] = list(payload.get("equal_low_levels") or [])[-10:]

    payload["overlay_policy"] = "luxalgo_rich_v1"
    payload["overlay_counts"] = {
        "order_blocks": len(payload["order_blocks"]),
        "fvgs": len(payload["fvgs"]),
        "swing_structures": len(payload["swing_structures"]),
        "internal_structures": len(payload["internal_structures"]),
        "equal_highs": len(payload["equal_highs"]),
        "equal_lows": len(payload["equal_lows"]),
    }
    return payload


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

    candles = [_serialize_candle(idx, row) for idx, row in df.iterrows()]
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
    # The runtime entry mode is shared with Scanner unless an older client
    # explicitly supplies one for backward compatibility.
    if entry_mode is None:
        from app.services.event_trigger import _get_cached_runtime_settings

        effective_entry_mode = _get_cached_runtime_settings().get("entry_mode", "limit")
    else:
        effective_entry_mode = entry_mode
    profiles = validate_timeframe_profiles(load_timeframe_profiles())
    configured_execution_timeframe = profiles["roles"]["trigger"]["timeframe"]
    is_execution_view = timeframe.lower() == configured_execution_timeframe.lower()

    if is_execution_view:
        try:
            execution = await execution_analyses.get(
                symbol=symbol,
                market_type=market_type,
                exchange=exchange,
                entry_mode=effective_entry_mode,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(
                status_code=502,
                detail="Execution analysis unavailable",
            ) from exc
        chart_frame = execution.frame.copy(deep=True)
        overlay_signal = execution.signal
        strategy_payload = execution.strategy.to_dict()
        is_actionable = execution.strategy.approved
        direction = execution.strategy.direction if is_actionable else "wait"
        setup_direction = execution.strategy.setup_direction
        analysis_metadata = execution.metadata()
        analysis_metadata["candle_policy"] = "closed_only"
    else:
        # Non-execution timeframes are chart research only.  They must not show
        # a 15M Strategy Gate beside candles from a different timeframe.
        try:
            chart_frame = await _market.get_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                market_type=market_type,
                exchange=exchange,
                limit=300,
                closed_only=True,
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=502, detail="Research analysis unavailable") from exc
        if chart_frame.empty:
            raise HTTPException(status_code=502, detail="No closed chart candles available")
        overlay_signal = _smc.analyze(
            chart_frame.copy(deep=True),
            symbol,
            timeframe,
            "neutral",
            entry_mode=effective_entry_mode,
        )
        strategy_payload = {
            "approved": False,
            "direction": "wait",
            "setup_direction": "wait",
            "rejection_reasons": [
                (
                    f"Research timeframe {timeframe} cannot authorize the "
                    f"{configured_execution_timeframe} execution Strategy Gate"
                )
            ],
            "effective_policy": {},
        }
        is_actionable = False
        direction = "wait"
        setup_direction = "wait"
        analysis_metadata = {
            "snapshot_id": None,
            "authority": "research_timeframe_only",
            "timeframe": timeframe,
            "last_closed_candle": chart_frame.index[-1].isoformat(),
            "candle_policy": "closed_only",
        }

    # Analysis remains closed-candle-only, while the chart receives the
    # exchange's distinct forming candle. Never merge a same-timestamp row
    # into the snapshot: that would repaint a bar already used by SMC.
    forming_candle = None
    try:
        chart_tail = await _market.get_ohlcv(
            symbol=symbol,
            timeframe=timeframe,
            market_type=market_type,
            exchange=exchange,
            limit=5,
            include_forming=True,
        )
        if not chart_tail.empty:
            newer = chart_tail.loc[chart_tail.index > chart_frame.index[-1]]
            if not newer.empty:
                forming_candle = _serialize_candle(newer.index[-1], newer.iloc[-1])
    except (ValueError, OSError):
        # A forming candle is presentation-only; its absence must not make a
        # valid closed-candle analysis unavailable.
        forming_candle = None

    # Fail closed at the API boundary. Raw SMC geometry may exist for analysis,
    # but no client receives executable-looking levels while Strategy Gate is
    # rejected. This prevents stale/local UI state from presenting a pending
    # order that the canonical decision never approved.
    execution_levels = _approved_execution_levels(overlay_signal, is_actionable)

    swing_highs = [
        {"t": sp.timestamp.isoformat(), "price": sp.price}
        for sp in overlay_signal.swing_highs
    ]
    swing_lows = [
        {"t": sp.timestamp.isoformat(), "price": sp.price}
        for sp in overlay_signal.swing_lows
    ]

    candles = [_serialize_candle(idx, row) for idx, row in chart_frame.iterrows()]
    clean_overlay = _clean_smc_overlay(overlay_signal)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "htf_timeframe": None,
        "analysis_snapshot": analysis_metadata,
        "ai_review": overlay_signal.ai_review,
        "decision_authority": "execution_timeframe_only",
        "candles": candles,
        "forming_candle": forming_candle,
        "bias": overlay_signal.bias,
        "htf_bias": "neutral",
        "confluence": overlay_signal.confluence_score,
        "indicator_decision": overlay_signal.indicator_decision,
        "tri_core_setup": overlay_signal.tri_core_setup,
        "trigger_state": overlay_signal.tri_core_setup.get("trigger_state", "wait") if isinstance(overlay_signal.tri_core_setup, dict) else "wait",
        "event_id": overlay_signal.tri_core_setup.get("event_id", "") if isinstance(overlay_signal.tri_core_setup, dict) else "",
        "volume_delta": overlay_signal.volume_delta,
        "delta_ratio": overlay_signal.delta_ratio,
        "cvd": overlay_signal.cvd,
        "cvd_divergence": overlay_signal.cvd_divergence,
        "cvd_divergence_evidence": overlay_signal.cvd_divergence_evidence,
        "delta_absorption": overlay_signal.delta_absorption,
        "delta_absorption_type": overlay_signal.delta_absorption_type,
        "delta_absorption_evidence": overlay_signal.delta_absorption_evidence,
        "delta_source": overlay_signal.delta_source,
        "flow_source": overlay_signal.flow_source,
        "volume_quality": overlay_signal.volume_quality,
        "market_regime": overlay_signal.market_regime,
        "overlay_indicator_decision": overlay_signal.indicator_decision,
        "overlay_market_regime": overlay_signal.market_regime,
        "bos": overlay_signal.bos,
        "choch": overlay_signal.choch,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "order_block": clean_overlay["order_block"],
        "fvg": clean_overlay["fvg"],
        "order_blocks": clean_overlay["order_blocks"],
        "breaker_blocks": clean_overlay.get("breaker_blocks", []),
        "fvgs": clean_overlay["fvgs"],
        "swing_structures": clean_overlay["swing_structures"],
        "internal_structures": clean_overlay["internal_structures"],
        "strong_weak_high": overlay_signal.strong_weak_high,
        "strong_weak_low": overlay_signal.strong_weak_low,
        "equal_highs": clean_overlay["equal_highs"],
        "equal_lows": clean_overlay["equal_lows"],
        "equal_high_levels": clean_overlay["equal_high_levels"],
        "equal_low_levels": clean_overlay["equal_low_levels"],
        "overlay_policy": clean_overlay["overlay_policy"],
        "overlay_counts": clean_overlay["overlay_counts"],
        "in_discount": overlay_signal.in_discount,
        "in_premium": overlay_signal.in_premium,
        "in_equilibrium": overlay_signal.in_equilibrium,
        "zone_position": overlay_signal.zone_position,
        "premium_zone": overlay_signal.premium_zone,
        "discount_zone": overlay_signal.discount_zone,
        "equilibrium_zone": overlay_signal.equilibrium_zone,
        "equilibrium": overlay_signal.equilibrium,
        "current_price": overlay_signal.current_price,
        "liquidity_swept": overlay_signal.liquidity_swept,
        "sweep_direction": overlay_signal.sweep_direction,
        "sweep_price": overlay_signal.sweep_price,
        "direction": direction,
        "setup_direction": setup_direction,
        "strategy": strategy_payload,
        "actionable": is_actionable,
        "execution_timeframe": configured_execution_timeframe,
        "decision_snapshot_id": (
            analysis_metadata.get("snapshot_id") if is_execution_view else None
        ),
        "order_flow": getattr(overlay_signal, "order_flow", {}),
        "derivatives_sentiment": getattr(overlay_signal, "derivatives_sentiment", {}),
        "inducements": getattr(overlay_signal, "inducements", []),
        "inducement_swept": getattr(overlay_signal, "inducement_swept", False),
        "active_zone_type": getattr(overlay_signal, "active_zone_type", "regular"),
        "hmm_regime": (
            getattr(overlay_signal, "market_regime", {}).get("metrics", {}).get("hmm_dominant_state")
            or getattr(overlay_signal, "hmm_regime", None)
        ),
        "hmm_probabilities": (
            getattr(overlay_signal, "market_regime", {}).get("metrics", {}).get("hmm_probabilities", {})
            or getattr(overlay_signal, "hmm_probabilities", {})
        ),
        **execution_levels,
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
