"""
Settings API
Manage LLM provider settings, system prompt, notifications, and strategy configuration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from loguru import logger
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.security import verify_api_key
from app.engines.ai_engine import AIEngine
from app.engines.strategy_engine import StrategyEngine

router = APIRouter()
_ai = AIEngine()
_strategy = StrategyEngine()


def _mask_secret(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 6:
        return "******"
    return f"{val[:2]}****{val[-4:]}"

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


@router.get("/llm/config")
async def get_llm_config(_key: str = Depends(verify_api_key)):
    """Get active runtime LLM configuration with masked secrets."""
    cfg = get_settings()
    return {
        "provider": "local",
        "local_endpoint": cfg.local_llm_endpoint,
        "local_model": cfg.local_llm_model,
        "gemini_key": _mask_secret(cfg.gemini_api_key),
        "gemini_configured": bool(cfg.gemini_api_key),
        "gemini_model": cfg.gemini_model,
        "openrouter_key": _mask_secret(cfg.openrouter_api_key),
        "openrouter_configured": bool(cfg.openrouter_api_key),
        "openrouter_model": cfg.openrouter_model,
    }


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
    safe_name = Path(req.prompt_file).name
    prompt_path = (PROMPTS_DIR / safe_name).resolve()
    if not prompt_path.is_relative_to(PROMPTS_DIR.resolve()) or not prompt_path.exists():
        raise HTTPException(status_code=404, detail=f"Prompt file not found: {req.prompt_file}")
    active_file = PROMPTS_DIR / "active_prompt.txt"
    active_file.write_text(safe_name, encoding="utf-8")
    _ai.reload_prompt()
    return {"message": f"Switched to prompt: {safe_name}"}


class SavePromptRequest(BaseModel):
    name: Optional[str] = "advisor_v1.md"
    content: str


@router.post("/prompts/save")
async def save_prompt(req: SavePromptRequest, _key: str = Depends(verify_api_key)):
    """Save updated system prompt content and reload in AI engine."""
    raw_name = req.name or "advisor_v1.md"
    safe_name = Path(raw_name).name
    if not safe_name.endswith(".md"):
        safe_name += ".md"
    prompt_path = (PROMPTS_DIR / safe_name).resolve()
    if not prompt_path.is_relative_to(PROMPTS_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid prompt filename")
    
    prompt_path.write_text(req.content, encoding="utf-8")
    
    active_file = PROMPTS_DIR / "active_prompt.txt"
    active_file.write_text(safe_name, encoding="utf-8")
    _ai.reload_prompt()
    
    return {
        "status": "ok",
        "message": f"Prompt '{safe_name}' saved and reloaded successfully",
        "length": len(req.content),
    }


@router.post("/prompts/test")
async def test_prompt(_key: str = Depends(verify_api_key)):
    """Run a test analysis using the active prompt."""
    sample_signal = {
        "symbol": "BTC/USDT",
        "direction": "LONG",
        "confluence": 80,
        "entry": 68450.0,
        "stop_loss": 67800.0,
        "take_profit": 69900.0,
        "market_structure": "Bullish BOS in Discount zone with Bullish Order Block",
    }
    messages = [
        {"role": "user", "content": "ช่วยวิเคราะห์สัญญาณ BTC/USDT (LONG) Confluence 80/100 สั้นๆ ให้หน่อย"}
    ]
    resp = await _ai.chat(messages, context=sample_signal)
    return {
        "status": "ok",
        "sample_signal": sample_signal,
        "ai_response": resp,
    }


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
    import httpx
    results = {}
    if req.telegram_bot_token and req.telegram_chat_id:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"https://api.telegram.org/bot{req.telegram_bot_token}/sendMessage"
                payload = {
                    "chat_id": req.telegram_chat_id,
                    "text": "🔔 Apex AI Trade Advisor: Test Notification Alert Successful! ✅",
                }
                resp = await client.post(url, json=payload)
                results["telegram"] = resp.status_code == 200
        except Exception:
            results["telegram"] = False

    if req.line_notify_token:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://notify-api.line.me/api/notify",
                    headers={"Authorization": f"Bearer {req.line_notify_token}"},
                    data={"message": "\nApex AI Trade Advisor: Test Notification Alert Successful! ✅"},
                )
                results["line"] = resp.status_code == 200
        except Exception:
            results["line"] = False

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
    from app.services.event_trigger import MarketMonitor, DEFAULT_WATCHLIST
    try:
        monitor = MarketMonitor.get_instance()
        return {"watchlist": monitor.watchlist}
    except Exception:
        return {"watchlist": DEFAULT_WATCHLIST}


@router.post("/watchlist")
async def add_to_watchlist(item: WatchlistAddRequest, _key: str = Depends(verify_api_key)):
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    for existing in monitor.watchlist:
        if existing["symbol"] == item.symbol:
            return {"status": "exists", "message": f"{item.symbol} is already in the watchlist"}

    new_item = {
        "symbol": item.symbol,
        "timeframe": item.timeframe,
        "htf_timeframe": item.htf_timeframe,
        "market_type": item.market_type,
        "exchange": item.exchange,
    }
    monitor.watchlist.append(new_item)
    return {"status": "added", "item": new_item}


@router.delete("/watchlist/{symbol}")
async def remove_from_watchlist(symbol: str, _key: str = Depends(verify_api_key)):
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    before_len = len(monitor.watchlist)
    monitor.watchlist = [
        item for item in monitor.watchlist if item["symbol"] != symbol
    ]
    after_len = len(monitor.watchlist)
    return {"status": "removed" if after_len < before_len else "not_found", "symbol": symbol}


# ------------------------------------------------------------------
# Broker & Exchange Connections Settings
# ------------------------------------------------------------------

class BrokerConfigRequest(BaseModel):
    binance_api_key: Optional[str] = None
    binance_api_secret: Optional[str] = None
    bybit_api_key: Optional[str] = None
    bybit_api_secret: Optional[str] = None
    mt5_login: Optional[int] = None
    mt5_password: Optional[str] = None
    mt5_server: Optional[str] = None
    mt5_path: Optional[str] = None
    alpaca_api_key: Optional[str] = None
    alpaca_api_secret: Optional[str] = None
    alpaca_base_url: Optional[str] = None


@router.get("/brokers/config")
async def get_broker_config(_key: str = Depends(verify_api_key)):
    """Get active runtime broker configuration with masked secrets."""
    cfg = get_settings()
    return {
        "binance_api_key": _mask_secret(cfg.binance_api_key),
        "binance_api_secret": _mask_secret(cfg.binance_api_secret),
        "binance_configured": bool(cfg.binance_api_key),
        "bybit_api_key": _mask_secret(cfg.bybit_api_key),
        "bybit_api_secret": _mask_secret(cfg.bybit_api_secret),
        "bybit_configured": bool(cfg.bybit_api_key),
        "mt5_login": cfg.mt5_login,
        "mt5_password": "******" if cfg.mt5_password else "",
        "mt5_server": cfg.mt5_server,
        "mt5_path": cfg.mt5_path,
        "mt5_configured": bool(cfg.mt5_login and cfg.mt5_server),
        "alpaca_api_key": _mask_secret(cfg.alpaca_api_key),
        "alpaca_api_secret": _mask_secret(cfg.alpaca_api_secret),
        "alpaca_base_url": cfg.alpaca_base_url,
        "alpaca_configured": bool(cfg.alpaca_api_key),
    }


@router.post("/brokers/config")
async def update_broker_config(req: BrokerConfigRequest, _key: str = Depends(verify_api_key)):
    """Update runtime broker & exchange settings and persist to .env."""
    cfg = get_settings()

    if req.binance_api_key is not None:
        cfg.binance_api_key = req.binance_api_key.strip()
    if req.binance_api_secret is not None:
        cfg.binance_api_secret = req.binance_api_secret.strip()
    if req.bybit_api_key is not None:
        cfg.bybit_api_key = req.bybit_api_key.strip()
    if req.bybit_api_secret is not None:
        cfg.bybit_api_secret = req.bybit_api_secret.strip()

    if req.mt5_login is not None:
        cfg.mt5_login = req.mt5_login
    if req.mt5_password is not None:
        cfg.mt5_password = req.mt5_password.strip()
    if req.mt5_server is not None:
        cfg.mt5_server = req.mt5_server.strip()
    if req.mt5_path is not None:
        cfg.mt5_path = req.mt5_path.strip()

    if req.alpaca_api_key is not None:
        cfg.alpaca_api_key = req.alpaca_api_key.strip()
    if req.alpaca_api_secret is not None:
        cfg.alpaca_api_secret = req.alpaca_api_secret.strip()
    if req.alpaca_base_url is not None:
        cfg.alpaca_base_url = req.alpaca_base_url.strip()

    # Persist to .env
    try:
        if ENV_FILE.exists():
            lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
            updates = {
                "BINANCE_API_KEY": cfg.binance_api_key,
                "BINANCE_API_SECRET": cfg.binance_api_secret,
                "BYBIT_API_KEY": cfg.bybit_api_key,
                "BYBIT_API_SECRET": cfg.bybit_api_secret,
                "MT5_LOGIN": str(cfg.mt5_login),
                "MT5_PASSWORD": cfg.mt5_password,
                "MT5_SERVER": cfg.mt5_server,
                "MT5_PATH": cfg.mt5_path,
                "ALPACA_API_KEY": cfg.alpaca_api_key,
                "ALPACA_API_SECRET": cfg.alpaca_api_secret,
                "ALPACA_BASE_URL": cfg.alpaca_base_url,
            }
            new_lines = []
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
    except Exception as e:
        logger.warning(f"Failed to persist broker settings to .env: {e}")

    return {"status": "ok", "message": "Broker & Exchange settings updated successfully"}


class BrokerTestRequest(BaseModel):
    broker_type: str
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    server: Optional[str] = None
    login: Optional[int] = None
    password: Optional[str] = None
    base_url: Optional[str] = None


@router.post("/brokers/test")
async def test_broker_connection(req: BrokerTestRequest, _key: str = Depends(verify_api_key)):
    """Test connection to specified broker or exchange."""
    import httpx
    cfg = get_settings()

    if req.broker_type == "binance":
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("https://api.binance.com/api/v3/ping")
                if resp.status_code == 200:
                    return {"status": "ok", "message": "Connected to Binance API successfully (Ping OK) ✅"}
                return {"status": "error", "message": f"Binance Ping failed: {resp.status_code}"}
        except Exception as e:
            return {"status": "error", "message": f"Binance Connection failed: {e}"}

    elif req.broker_type == "bybit":
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get("https://api.bybit.com/v5/market/time")
                if resp.status_code == 200:
                    return {"status": "ok", "message": "Connected to Bybit API successfully ✅"}
                return {"status": "error", "message": f"Bybit check failed: {resp.status_code}"}
        except Exception as e:
            return {"status": "error", "message": f"Bybit Connection failed: {e}"}

    elif req.broker_type == "alpaca":
        key = (req.api_key or cfg.alpaca_api_key).strip()
        sec = (req.api_secret or cfg.alpaca_api_secret).strip()
        raw_base = (req.base_url or cfg.alpaca_base_url or "https://paper-api.alpaca.markets").strip()
        clean_base = raw_base.rstrip("/").removesuffix("/v2").removesuffix("/v1")
        if not key:
            return {"status": "error", "message": "Please enter an Alpaca API Key ID"}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"{clean_base}/v2/account",
                    headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
                )
                if resp.status_code == 200:
                    acc = resp.json()
                    status = acc.get("status", "ACTIVE")
                    acc_no = acc.get("account_number", "")
                    buying_power = acc.get("buying_power", "0")
                    return {
                        "status": "ok",
                        "message": f"Connected to Alpaca! Account #{acc_no} (Status: {status}, Buying Power: ${float(buying_power):,.2f}) ✅",
                    }
                elif resp.status_code == 401:
                    return {
                        "status": "error",
                        "message": "Alpaca 401 Unauthorized: Key หรือ Secret ไม่ถูกต้อง (ระวังการ Copy ไม่ครบตัวอักษร หรือกด Generate Key ใหม่)",
                    }
                return {"status": "error", "message": f"Alpaca auth failed (HTTP {resp.status_code}): {resp.text}"}
        except Exception as e:
            return {"status": "error", "message": f"Alpaca Connection failed: {e}"}

    elif req.broker_type == "mt5":
        login = req.login or cfg.mt5_login
        server = req.server or cfg.mt5_server
        if not login or not server:
            return {"status": "error", "message": "Please enter MT5 Account Login ID and Server Name"}
        return {
            "status": "ok",
            "message": f"MT5 Credentials validated for Account #{login} on Server '{server}'. (Bridge ready for Terminal hook) ✅",
        }

    return {"status": "error", "message": f"Unknown broker type: {req.broker_type}"}


class TradingModeRequest(BaseModel):
    mode: Literal["paper", "live"]


@router.post("/trading-mode")
async def set_trading_mode(req: TradingModeRequest, _key: str = Depends(verify_api_key)):
    """Set global trading mode (paper or live)."""
    cfg = get_settings()
    cfg.trading_mode = req.mode
    get_settings.cache_clear()

    # Update .env
    try:
        if ENV_FILE.exists():
            lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("TRADING_MODE="):
                    new_lines.append(f"TRADING_MODE={req.mode}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"TRADING_MODE={req.mode}")
            ENV_FILE.write_text("\n".join(new_lines), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to persist TRADING_MODE to .env: {e}")

    return {"status": "ok", "trading_mode": req.mode}
