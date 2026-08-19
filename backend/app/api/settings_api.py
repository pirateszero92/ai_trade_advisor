"""
Settings API
Manage LLM provider settings, system prompt, notifications, and strategy configuration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.security import verify_api_key
from app.engines.ai_engine import AIEngine
from app.engines.strategy_engine import StrategyEngine

router = APIRouter()
_ai = AIEngine()
_strategy = StrategyEngine()

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
ENV_FILE = Path(__file__).parent.parent.parent / ".env"
if not ENV_FILE.exists():
    ENV_FILE = Path(__file__).parent.parent.parent / "backend" / ".env"


class PromptSwitchRequest(BaseModel):
    prompt_file: str  # e.g. "advisor_v1.md"


class ChatRequest(BaseModel):
    messages: list[dict]
    context: Optional[dict] = None


class LLMTestRequest(BaseModel):
    provider: str  # "local", "gemini", "openrouter"
    endpoint: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None


class LLMConfigRequest(BaseModel):
    provider: Optional[str] = "local"
    local_endpoint: Optional[str] = None
    local_model: Optional[str] = None
    gemini_key: Optional[str] = None
    gemini_model: Optional[str] = None
    openrouter_key: Optional[str] = None
    openrouter_model: Optional[str] = None


# ------------------------------------------------------------------
# LLM Provider Management
# ------------------------------------------------------------------

@router.get("/llm/providers")
async def list_providers(_key: str = Depends(verify_api_key)):
    """List configured LLM providers and their current status."""
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


@router.post("/llm/test")
async def test_llm_config(req: LLMTestRequest, _key: str = Depends(verify_api_key)):
    """Live test connectivity to a provider with specified endpoint, model, or key."""
    result = await _ai.test_connection(
        provider=req.provider,
        custom_endpoint=req.endpoint,
        custom_model=req.model,
        custom_key=req.api_key,
    )
    return result


@router.get("/llm/test/{provider}")
async def test_provider_get(provider: str, _key: str = Depends(verify_api_key)):
    """Legacy GET endpoint for testing connectivity to configured provider."""
    result = await _ai.test_connection(provider=provider)
    return result


@router.get("/llm/discover")
async def discover_models(_key: str = Depends(verify_api_key)):
    """Discover available models on local Ollama and LM Studio instances."""
    return await _ai.discover_local_models()


@router.post("/llm/config")
async def update_llm_config(req: LLMConfigRequest, _key: str = Depends(verify_api_key)):
    """Update runtime LLM settings in memory and persist to .env."""
    cfg = get_settings()
    
    if req.local_endpoint is not None:
        cfg.local_llm_endpoint = req.local_endpoint.strip()
    if req.local_model is not None:
        cfg.local_llm_model = req.local_model.strip()
    if req.gemini_key is not None:
        cfg.gemini_api_key = req.gemini_key.strip()
    if req.gemini_model is not None:
        cfg.gemini_model = req.gemini_model.strip()
    if req.openrouter_key is not None:
        cfg.openrouter_api_key = req.openrouter_key.strip()
    if req.openrouter_model is not None:
        cfg.openrouter_model = req.openrouter_model.strip()

    # Persist to .env if file exists
    try:
        if ENV_FILE.exists():
            lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
            new_lines = []
            updates = {
                "LOCAL_LLM_ENDPOINT": cfg.local_llm_endpoint,
                "LOCAL_LLM_MODEL": cfg.local_llm_model,
                "GEMINI_API_KEY": cfg.gemini_api_key,
                "GEMINI_MODEL": cfg.gemini_model,
                "OPENROUTER_API_KEY": cfg.openrouter_api_key,
                "OPENROUTER_MODEL": cfg.openrouter_model,
            }
            matched_keys = set()
            for line in lines:
                key = line.split("=")[0].strip() if "=" in line else None
                if key in updates:
                    new_lines.append(f"{key}={updates[key]}")
                    matched_keys.add(key)
                else:
                    new_lines.append(line)
            
            for key, val in updates.items():
                if key not in matched_keys:
                    new_lines.append(f"{key}={val}")

            ENV_FILE.write_text("\n".join(new_lines), encoding="utf-8")
    except Exception:
        pass

    return {
        "status": "ok",
        "message": "LLM configuration updated successfully",
        "current": {
            "local_endpoint": cfg.local_llm_endpoint,
            "local_model": cfg.local_llm_model,
            "gemini_model": cfg.gemini_model,
            "openrouter_model": cfg.openrouter_model,
        }
    }


@router.post("/llm/chat")
async def chat(
    req: ChatRequest,
    _key: str = Depends(verify_api_key),
):
    """Free-form chat with the AI advisor."""
    response = await _ai.chat(req.messages, context=req.context)
    return {"response": response}


# ------------------------------------------------------------------
# Prompts
# ------------------------------------------------------------------

@router.get("/prompts")
async def list_prompts(_key: str = Depends(verify_api_key)):
    files = [f.name for f in PROMPTS_DIR.glob("*.md")]
    active_file = PROMPTS_DIR / "active_prompt.txt"
    active = active_file.read_text(encoding="utf-8").strip() if active_file.exists() else "unknown"
    return {"prompts": files, "active": active}


@router.get("/prompts/active")
async def get_active_prompt(_key: str = Depends(verify_api_key)):
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
    prompt_path = PROMPTS_DIR / req.prompt_file
    if not prompt_path.exists():
        raise HTTPException(status_code=404, detail=f"Prompt file not found: {req.prompt_file}")
    active_file = PROMPTS_DIR / "active_prompt.txt"
    active_file.write_text(req.prompt_file, encoding="utf-8")
    _ai.reload_prompt()
    return {"message": f"Switched to prompt: {req.prompt_file}"}


@router.post("/prompts/reload")
async def reload_prompt(_key: str = Depends(verify_api_key)):
    prompt = _ai.reload_prompt()
    return {"message": "Prompt reloaded", "length": len(prompt)}


# ------------------------------------------------------------------
# Strategy
# ------------------------------------------------------------------

@router.post("/strategy/reload")
async def reload_strategy(_key: str = Depends(verify_api_key)):
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
    from app.services.notification_service import NotificationService
    notifier = NotificationService()

    results = {}
    if req.telegram_bot_token and req.telegram_chat_id:
        results["telegram"] = await notifier.send_telegram(
            message="🔔 AI Trade Advisor: Test alert successful!",
            token=req.telegram_bot_token,
            chat_id=req.telegram_chat_id,
        )
    if req.line_notify_token:
        results["line"] = await notifier.send_line(
            message="AI Trade Advisor: Test alert successful!",
            token=req.line_notify_token,
        )

    return {"status": "ok", "results": results}


# ------------------------------------------------------------------
# Proactive Scanner Watchlist Settings
# ------------------------------------------------------------------

class WatchlistAddRequest(BaseModel):
    symbol: str
    market_type: str = "crypto"
    timeframe: str = "1h"
    htf_timeframe: str = "4h"
    exchange: str = "binance"


@router.get("/watchlist")
async def get_watchlist(_key: str = Depends(verify_api_key)):
    from app.services.event_trigger import EventTriggerService
    return {"watchlist": EventTriggerService.WATCHLIST}


@router.post("/watchlist")
async def add_to_watchlist(item: WatchlistAddRequest, _key: str = Depends(verify_api_key)):
    from app.services.event_trigger import EventTriggerService
    for existing in EventTriggerService.WATCHLIST:
        if existing["symbol"] == item.symbol:
            return {"status": "exists", "message": f"{item.symbol} is already in the watchlist"}

    new_item = {
        "symbol": item.symbol,
        "timeframe": item.timeframe,
        "htf_timeframe": item.htf_timeframe,
        "market_type": item.market_type,
        "exchange": item.exchange,
    }
    EventTriggerService.WATCHLIST.append(new_item)
    return {"status": "added", "item": new_item}


@router.delete("/watchlist/{symbol}")
async def remove_from_watchlist(symbol: str, _key: str = Depends(verify_api_key)):
    from app.services.event_trigger import EventTriggerService
    before_len = len(EventTriggerService.WATCHLIST)
    EventTriggerService.WATCHLIST = [
        item for item in EventTriggerService.WATCHLIST if item["symbol"] != symbol
    ]
    after_len = len(EventTriggerService.WATCHLIST)
    return {"status": "removed" if after_len < before_len else "not_found", "symbol": symbol}
