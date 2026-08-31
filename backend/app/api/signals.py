"""
Signals API
Triggers full SMC + AI analysis for a given symbol and timeframe.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.security import verify_api_key
from app.engines.ai_engine import AIEngine
from app.engines.market_data import MarketDataEngine
from app.engines.risk_engine import RiskEngine
from app.engines.smc_engine import SMCEngine
from app.engines.strategy_engine import StrategyEngine
from app.services.evidence import capture_decision_evidence
from app.services.execution_analysis import execution_analyses
from app.services.mtf_analysis import mtf_analyses  # research-only endpoint

router = APIRouter()

# Shared engine instances (lightweight; stateless)
_smc = SMCEngine()
_ai = AIEngine()
_risk = RiskEngine()
_strategy = StrategyEngine()
_market = MarketDataEngine()


class AnalyseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(default="BTC/USDT", min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$")
    timeframe: Literal["1m", "2m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"] = "1h"
    market_type: Literal["crypto", "forex", "stock"] = "crypto"
    exchange: Literal["binance", "bybit", "innovestx", "mt5", "alpaca", "yfinance"] = "binance"
    htf_bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    limit: int = Field(default=500, ge=20, le=1500)
    account_balance: float = Field(default=10_000.0, gt=0, le=1_000_000_000, allow_inf_nan=False)
    open_positions: int = Field(default=0, ge=0, le=1000)
    daily_pnl_pct: float = Field(default=0.0, ge=-100, le=1000, allow_inf_nan=False)
    drawdown_pct: float = Field(default=0.0, ge=0, le=100, allow_inf_nan=False)
    market_context: Optional[str] = Field(default=None, max_length=8000)
    skip_ai: bool = False


@router.post("/analyse")
@router.post("/analyze")
async def analyse_signal(
    req: AnalyseRequest,
    _key: str = Depends(verify_api_key),
):
    """
    Run full SMC analysis, strategy evaluation, risk assessment,
    and optional AI analysis for the requested symbol.
    """
    # The execution timeframe is the only decision authority. The legacy
    # MTF service currently supplies that closed-candle snapshot, but its
    # bias/setup stages are excluded from scoring, risk and evidence.
    try:
        execution = await execution_analyses.get(
            symbol=req.symbol,
            market_type=req.market_type,
            exchange=req.exchange,
            entry_mode="limit",
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"Execution analysis unavailable: {exc}") from exc
    signal = execution.signal
    strategy_result = execution.strategy
    df = execution.frame

    # 4. Risk assessment
    risk = _risk.evaluate(
        signal,
        account_balance=req.account_balance,
        open_positions=req.open_positions,
        daily_pnl_pct=req.daily_pnl_pct,
        drawdown_pct=req.drawdown_pct,
    )

    # 5. AI analysis (optional)
    ai_result = None
    if not req.skip_ai and strategy_result.approved and risk.approved:
        portfolio_state = {
            "balance": req.account_balance,
            "open_positions": req.open_positions,
            "daily_pnl_pct": req.daily_pnl_pct,
            "drawdown_pct": req.drawdown_pct,
        }
        ai_result = (
            await _ai.analyze(signal, portfolio_state, req.market_context)
        ).to_dict()

    risk_payload = {
        "approved": risk.approved,
        "rejection_reason": risk.rejection_reason,
        "position_size": risk.position_size,
        "risk_pct": risk.risk_pct,
        "risk_amount": risk.risk_amount,
        "base_risk_pct": risk.base_risk_pct,
        "regime_risk_multiplier": risk.regime_risk_multiplier,
        "market_regime": risk.market_regime,
        "tone": risk.tone,
        "warnings": risk.warnings,
        "inputs": {
            "account_balance": req.account_balance,
            "open_positions": req.open_positions,
            "daily_pnl_pct": req.daily_pnl_pct,
            "drawdown_pct": req.drawdown_pct,
        },
    }
    evidence = await capture_decision_evidence(
        source="manual_analysis",
        symbol=req.symbol,
        timeframe=execution.timeframe,
        market_type=req.market_type,
        exchange=req.exchange,
        market_data=df,
        htf_bias="neutral",
        entry_mode=signal.entry_type,
        signal=signal.to_dict(),
        strategy=strategy_result.to_dict(),
        risk=risk_payload,
        ai_analysis=ai_result,
        config_snapshot=execution.config_snapshot,
        mtf_market_data=None,
        mtf_decision=None,
    )

    return {
        "signal": signal.to_dict(),
        "strategy": strategy_result.to_dict(),
        "risk": risk_payload,
        "ai_analysis": ai_result,
        "decision_authority": "execution_timeframe_only",
        "evidence": evidence,
    }


@router.get("/quick")
async def quick_signal(
    symbol: str = Query("BTC/USDT", min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$"),
    timeframe: Literal["1m", "2m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"] = Query("1h"),
    market_type: Literal["crypto", "forex", "stock"] = Query("crypto"),
    htf_bias: Literal["bullish", "bearish", "neutral"] = Query("neutral"),
    _key: str = Depends(verify_api_key),
):
    """Fast signal check — SMC only, no AI, no risk."""
    df = await _market.get_ohlcv(symbol=symbol, timeframe=timeframe, market_type=market_type)
    if df.empty:
        raise HTTPException(status_code=502, detail="Failed to fetch market data")
    signal = _smc.analyze(df, symbol, timeframe, htf_bias)
    return signal.to_dict()


@router.get("/mtf-matrix")
async def get_mtf_matrix(
    symbol: str = Query("BTC/USDT", min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$"),
    market_type: Literal["crypto", "forex", "stock"] = Query("crypto"),
    exchange: Optional[Literal["binance", "bybit", "innovestx", "mt5", "alpaca", "yfinance"]] = Query(None),
    entry_mode: Literal["limit", "market"] = Query("limit"),
    _key: str = Depends(verify_api_key),
):
    """Return an informational 4H/1H/15m matrix; it never affects entry gates."""
    resolved_exchange = exchange or {
        "crypto": "binance",
        "forex": "mt5",
        "stock": "alpaca",
    }[market_type]
    try:
        mtf = await mtf_analyses.get(
            symbol=symbol,
            market_type=market_type,
            exchange=resolved_exchange,
            entry_mode=entry_mode,
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"MTF analysis unavailable: {exc}") from exc
    return {"status": "ok", "data": mtf.to_dict()}


@router.get("/")
async def list_signals(
    limit: int = Query(50, ge=1, le=100),
    market_type: Optional[Literal["crypto", "forex", "stock", "all"]] = Query(None),
    mode: Optional[Literal["paper", "live"]] = Query(None),
    _key: str = Depends(verify_api_key),
):
    """List recent high-confluence SMC signals detected by proactive monitor (strictly filtered to Settings watchlist and active mode)."""
    from app.services.event_trigger import MarketMonitor, _clean_message_text, _compact_symbol
    monitor = MarketMonitor.get_instance()
    active_norm_symbols = {
        _compact_symbol(w.get("symbol", ""))
        for w in monitor.watchlist if w.get("symbol")
    }
    signals = [
        dict(s) for s in monitor.recent_signals
        if _compact_symbol(s.get("symbol", "")) in active_norm_symbols
    ]
    if mode == "live":
        signals = [s for s in signals if s.get("exchange") == "innovestx" or "thb" in s.get("symbol", "").lower()]
    elif mode == "paper":
        signals = [s for s in signals if s.get("exchange") != "innovestx" and "thb" not in s.get("symbol", "").lower()]

    if market_type and market_type.lower() != "all":
        mt = market_type.lower()
        signals = [s for s in signals if s.get("market_type", "crypto").lower() == mt]

    signals = signals[:limit]

    for s in signals:
        if "message" in s:
            s["message"] = _clean_message_text(s["message"])

    return {
        "total": len(signals),
        "last_scan": monitor.last_scan_time,
        "running": monitor.running,
        "mode": mode,
        "signals": signals,
    }


@router.get("/live-prices")
async def get_live_prices(
    mode: Optional[Literal["paper", "live"]] = Query(None),
    _key: str = Depends(verify_api_key),
):
    """Fetch realtime live prices for Settings watchlist symbols only."""
    from app.engines.price_hub import price_hub
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    
    all_syms = set()
    for w in monitor.watchlist:
        if w.get("symbol"):
            sym = w["symbol"]
            m_type = w.get("market_type", "crypto")
            ex = w.get("exchange", "")
            is_live_asset = ex == "innovestx" or "thb" in sym.lower()
            if mode == "live" and not is_live_asset:
                continue
            if mode == "paper" and is_live_asset:
                continue
            all_syms.add((sym, m_type))

    if not all_syms:
        return {"status": "ok", "prices": {}}

    prices = {}

    for sym, _market_type in all_syms:
        price_hub.register_symbol(sym)
        ticker = price_hub.get_ticker(sym) or {}
        p = float(ticker.get("price", 0.0))
        chg = float(ticker.get("change_24h", 0.0))
        if p > 0:
            prices[sym] = {"price": p, "change_24h": chg, "source": ticker.get("source", "cache")}

    return {"status": "ok", "prices": prices}


@router.post("/scan")
async def trigger_scan(
    mode: Optional[Literal["paper", "live"]] = Query(None),
    _key: str = Depends(verify_api_key),
):
    """Trigger an immediate proactive scan of Settings watchlist markets only."""
    from app.services.event_trigger import MarketMonitor, _compact_symbol
    monitor = MarketMonitor.get_instance()
    new_signals = await monitor.scan_all()
    active_norm_symbols = {
        _compact_symbol(w.get("symbol", ""))
        for w in monitor.watchlist if w.get("symbol")
    }
    filtered_signals = [
        s for s in new_signals
        if _compact_symbol(s.get("symbol", "")) in active_norm_symbols
    ]
    if mode == "live":
        filtered_signals = [s for s in filtered_signals if s.get("exchange") == "innovestx" or "thb" in s.get("symbol", "").lower()]
    elif mode == "paper":
        filtered_signals = [s for s in filtered_signals if s.get("exchange") != "innovestx" and "thb" not in s.get("symbol", "").lower()]

    return {
        "message": f"Scan complete. {len(filtered_signals)} setups ({mode or 'all'}).",
        "new_count": len(filtered_signals),
        "total_signals": len(filtered_signals),
        "mode": mode,
        "signals": filtered_signals,
    }
