"""
Signals API
Triggers full SMC + AI analysis for a given symbol and timeframe.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.security import verify_api_key
from app.engines.ai_engine import AIEngine
from app.engines.market_data import MarketDataEngine
from app.engines.risk_engine import RiskEngine
from app.engines.smc_engine import SMCEngine
from app.engines.strategy_engine import StrategyEngine

router = APIRouter()

# Shared engine instances (lightweight; stateless)
_smc = SMCEngine()
_ai = AIEngine()
_risk = RiskEngine()
_strategy = StrategyEngine()
_market = MarketDataEngine()


class AnalyseRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1H"
    market_type: Literal["crypto", "forex", "stock"] = "crypto"
    exchange: str = "binance"
    htf_bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    limit: int = 500
    account_balance: float = 10_000.0
    open_positions: int = 0
    daily_pnl_pct: float = 0.0
    drawdown_pct: float = 0.0
    market_context: Optional[str] = None
    skip_ai: bool = False


@router.post("/analyse")
async def analyse_signal(
    req: AnalyseRequest,
    _key: str = Depends(verify_api_key),
):
    """
    Run full SMC analysis, strategy evaluation, risk assessment,
    and optional AI analysis for the requested symbol.
    """
    # 1. Fetch OHLCV
    df = await _market.get_ohlcv(
        symbol=req.symbol,
        timeframe=req.timeframe,
        market_type=req.market_type,
        exchange=req.exchange,
        limit=req.limit,
    )
    if df.empty:
        raise HTTPException(status_code=502, detail="Failed to fetch market data")

    # 2. SMC Analysis
    signal = _smc.analyze(df, req.symbol, req.timeframe, req.htf_bias)

    # 3. Strategy evaluation
    strategy_result = _strategy.evaluate(signal)

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

    return {
        "signal": signal.to_dict(),
        "strategy": strategy_result.to_dict(),
        "risk": {
            "approved": risk.approved,
            "rejection_reason": risk.rejection_reason,
            "position_size": risk.position_size,
            "risk_pct": risk.risk_pct,
            "risk_amount": risk.risk_amount,
            "tone": risk.tone,
            "warnings": risk.warnings,
        },
        "ai_analysis": ai_result,
    }


@router.get("/quick")
async def quick_signal(
    symbol: str = Query("BTC/USDT"),
    timeframe: str = Query("1H"),
    htf_bias: Literal["bullish", "bearish", "neutral"] = Query("neutral"),
    _key: str = Depends(verify_api_key),
):
    """Fast signal check — SMC only, no AI, no risk."""
    df = await _market.get_ohlcv(symbol=symbol, timeframe=timeframe)
    if df.empty:
        raise HTTPException(status_code=502, detail="Failed to fetch market data")
    signal = _smc.analyze(df, symbol, timeframe, htf_bias)
    return signal.to_dict()


@router.get("/")
async def list_signals(
    limit: int = Query(20, le=50),
    _key: str = Depends(verify_api_key),
):
    """List recent high-confluence SMC signals detected by proactive monitor."""
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    signals = monitor.recent_signals[:limit]

    # If empty, run an initial scan to populate
    if not signals:
        signals = await monitor.scan_all()

    return {
        "total": len(signals),
        "last_scan": monitor.last_scan_time,
        "running": monitor.running,
        "signals": signals,
    }


@router.post("/scan")
async def trigger_scan(
    _key: str = Depends(verify_api_key),
):
    """Trigger an immediate proactive scan across all watchlist markets."""
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    new_signals = await monitor.scan_all()
    return {
        "message": f"Scan complete. {len(new_signals)} new setups detected.",
        "new_count": len(new_signals),
        "total_signals": len(monitor.recent_signals),
        "signals": monitor.recent_signals,
    }
