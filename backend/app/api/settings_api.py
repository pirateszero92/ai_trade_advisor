"""
Settings API
Manage LLM provider settings, system prompt, and strategy configuration.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.security import verify_api_key
from app.engines.ai_engine import AIEngine
from app.engines.strategy_engine import StrategyEngine

router = APIRouter()
_ai = AIEngine()
_strategy = StrategyEngine()

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


class PromptSwitchRequest(BaseModel):
    prompt_file: str  # e.g. "advisor_v1.md"


class ChatRequest(BaseModel):
    messages: list[dict]


# ------------------------------------------------------------------
# LLM
# ------------------------------------------------------------------

@router.get("/llm/providers")
async def list_providers(_key: str = Depends(verify_api_key)):
    """List configured LLM providers and their current status."""
    from app.core.config import get_settings
    cfg = get_settings()
    return {
        "providers": [
            {
                "name": "local",
                "model": cfg.local_llm_model,
                "endpoint": cfg.local_llm_endpoint,
                "configured": True,
            },
            {
                "name": "gemini",
                "model": cfg.gemini_model,
                "configured": bool(cfg.gemini_api_key),
            },
            {
                "name": "openrouter",
                "model": cfg.openrouter_model,
                "configured": bool(cfg.openrouter_api_key),
            },
        ]
    }


@router.get("/llm/test/{provider}")
async def test_provider(
    provider: str,
    _key: str = Depends(verify_api_key),
):
    """Test connectivity to a specific LLM provider."""
    if provider not in ("local", "gemini", "openrouter"):
        raise HTTPException(status_code=400, detail="Unknown provider")
    result = await _ai.test_connection(provider)
    return result


@router.post("/llm/chat")
async def chat(
    req: ChatRequest,
    _key: str = Depends(verify_api_key),
):
    """Free-form chat with the AI advisor."""
    response = await _ai.chat(req.messages)
    return {"response": response}


# ------------------------------------------------------------------
# Prompts
# ------------------------------------------------------------------

@router.get("/prompts")
async def list_prompts(_key: str = Depends(verify_api_key)):
    """List all available prompt files."""
    files = [f.name for f in PROMPTS_DIR.glob("*.md")]
    active_file = PROMPTS_DIR / "active_prompt.txt"
    active = active_file.read_text(encoding="utf-8").strip() if active_file.exists() else "unknown"
    return {"prompts": files, "active": active}


@router.get("/prompts/active")
async def get_active_prompt(_key: str = Depends(verify_api_key)):
    """Return the full content of the active prompt."""
    prompt_name = _ai._active_prompt_file or "advisor_v1.md"
    prompt_path = PROMPTS_DIR / prompt_name
    if not prompt_path.exists():
        raise HTTPException(status_code=404, detail="Active prompt file not found")
    return {"name": prompt_name, "content": prompt_path.read_text(encoding="utf-8")}


@router.post("/prompts/switch")
async def switch_prompt(
    req: PromptSwitchRequest,
    _key: str = Depends(verify_api_key),
):
    """Switch the active prompt file."""
    prompt_path = PROMPTS_DIR / req.prompt_file
    if not prompt_path.exists():
        raise HTTPException(status_code=404, detail=f"Prompt file not found: {req.prompt_file}")
    active_file = PROMPTS_DIR / "active_prompt.txt"
    active_file.write_text(req.prompt_file, encoding="utf-8")
    _ai.reload_prompt()
    return {"message": f"Switched to prompt: {req.prompt_file}"}


@router.post("/prompts/reload")
async def reload_prompt(_key: str = Depends(verify_api_key)):
    """Reload the current prompt from disk."""
    prompt = _ai.reload_prompt()
    return {"message": "Prompt reloaded", "length": len(prompt)}


# ------------------------------------------------------------------
# Strategy
# ------------------------------------------------------------------

@router.post("/strategy/reload")
async def reload_strategy(_key: str = Depends(verify_api_key)):
    """Reload strategy rules from config/strategy.yaml."""
    _strategy.reload()
    return {"message": "Strategy reloaded", "name": _strategy._strategy.get("name")}


# ------------------------------------------------------------------
# Notifications Test
# ------------------------------------------------------------------

class TestNotificationRequest(BaseModel):
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    line_notify_token: str = ""


@router.post("/notifications/test")
async def test_notifications(
    req: TestNotificationRequest,
    _key: str = Depends(verify_api_key),
):
    """Send a test signal alert to configured Telegram and LINE channels."""
    from app.services.notification import NotificationService
    from app.core.config import get_settings
    cfg = get_settings()

    # Override temporary tokens if provided in request
    if req.telegram_bot_token:
        cfg.telegram_bot_token = req.telegram_bot_token
    if req.telegram_chat_id:
        cfg.telegram_chat_id = req.telegram_chat_id
    if req.line_notify_token:
        cfg.line_notify_token = req.line_notify_token

    notifier = NotificationService()
    results = await notifier.send_signal_alert(
        symbol="BTC/USDT",
        timeframe="1H",
        direction="long",
        message="[TEST ALERT] Apex AI Trade Advisor เชื่อมต่อระบบแจ้งเตือนสำเร็จแล้ว! พร้อมส่งสัญญาณ SMC แบบ Real-time",
        confluence_score=85,
        entry=64500.0,
        sl=63800.0,
        tp=66200.0,
        rr=2.4,
    )
    return {"message": "Test notification executed", "results": results}


# ------------------------------------------------------------------
# Watchlist Management
# ------------------------------------------------------------------

class WatchlistItemRequest(BaseModel):
    symbol: str
    market_type: str = "crypto"  # crypto, forex, stock
    timeframe: str = "1h"
    htf_timeframe: str = "4h"
    exchange: str = "binance"


@router.get("/watchlist")
async def get_watchlist(_key: str = Depends(verify_api_key)):
    """Get the active watchlist monitored by the proactive scanner."""
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    return {"watchlist": monitor.watchlist}


@router.post("/watchlist")
async def add_watchlist_item(
    req: WatchlistItemRequest,
    _key: str = Depends(verify_api_key),
):
    """Add a new symbol / market pair to the active scanner watchlist."""
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()

    # Check if already exists
    sym = req.symbol.strip().upper()
    existing = [x for x in monitor.watchlist if x["symbol"].upper() == sym]
    if existing:
        return {"message": f"Symbol {sym} already in watchlist", "watchlist": monitor.watchlist}

    item = {
        "symbol": sym,
        "market_type": req.market_type.lower(),
        "timeframe": req.timeframe.lower(),
        "htf_timeframe": req.htf_timeframe.lower(),
        "exchange": req.exchange.lower(),
    }
    monitor.watchlist.append(item)
    return {"message": f"Added {sym} to watchlist", "watchlist": monitor.watchlist}


@router.delete("/watchlist/{symbol:path}")
async def remove_watchlist_item(
    symbol: str,
    _key: str = Depends(verify_api_key),
):
    """Remove a symbol from the active scanner watchlist."""
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    sym = symbol.strip().upper()
    monitor.watchlist = [x for x in monitor.watchlist if x["symbol"].upper() != sym]
    return {"message": f"Removed {sym} from watchlist", "watchlist": monitor.watchlist}
