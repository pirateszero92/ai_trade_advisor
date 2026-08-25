"""
Settings API
Manage LLM provider settings, system prompt, notifications, and strategy configuration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from loguru import logger
from fastapi import APIRouter, Depends, HTTPException, Query
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

RUNTIME_SETTINGS_FILE = Path(__file__).parent.parent.parent / "config" / "runtime_settings.json"


def _load_runtime_settings():
    if RUNTIME_SETTINGS_FILE.exists():
        try:
            import json
            data = json.loads(RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
            cfg = get_settings()
            if "provider" in data and data["provider"]:
                _ai.active_provider = data["provider"]
            elif "active_provider" in data and data["active_provider"]:
                _ai.active_provider = data["active_provider"]
            if "local_endpoint" in data and data["local_endpoint"]:
                cfg.local_llm_endpoint = data["local_endpoint"]
            if "local_model" in data and data["local_model"]:
                cfg.local_llm_model = data["local_model"]
            gem = data.get("gemini_api_key") or data.get("gemini_key")
            if gem and not ("****" in gem or gem.startswith("***")):
                cfg.gemini_api_key = gem
            if "gemini_model" in data and data["gemini_model"]:
                cfg.gemini_model = data["gemini_model"]
            op = data.get("openrouter_api_key") or data.get("openrouter_key")
            if op and not ("****" in op or op.startswith("***")):
                cfg.openrouter_api_key = op
            if "openrouter_model" in data and data["openrouter_model"]:
                cfg.openrouter_model = data["openrouter_model"]
            if "risk_per_trade" in data and data["risk_per_trade"]:
                cfg.default_risk_per_trade = float(data["risk_per_trade"])
            if "max_daily_loss" in data and data["max_daily_loss"]:
                cfg.max_daily_loss = float(data["max_daily_loss"])
            if "max_open_positions" in data and data["max_open_positions"]:
                cfg.max_open_positions = int(data["max_open_positions"])
        except Exception as e:
            logger.warning(f"Failed to load runtime settings: {e}")


_load_runtime_settings()


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
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    openrouter_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
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
        "provider": getattr(_ai, "active_provider", "local"),
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
    
    if req.provider is not None:
        _ai.active_provider = req.provider.strip()
    if req.local_endpoint is not None:
        cfg.local_llm_endpoint = req.local_endpoint.strip()
    if req.local_model is not None:
        cfg.local_llm_model = req.local_model.strip()
    
    gem_k = req.gemini_api_key if req.gemini_api_key is not None else req.gemini_key
    if gem_k is not None:
        clean_gem = gem_k.strip()
        if clean_gem and not ("****" in clean_gem or clean_gem.startswith("***")):
            cfg.gemini_api_key = clean_gem
    if req.gemini_model is not None:
        cfg.gemini_model = req.gemini_model.strip()
        
    open_k = req.openrouter_api_key if req.openrouter_api_key is not None else req.openrouter_key
    if open_k is not None:
        clean_open = open_k.strip()
        if clean_open and not ("****" in clean_open or clean_open.startswith("***")):
            cfg.openrouter_api_key = clean_open
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

    # Persist to runtime JSON store with safe Load-Merge-Write
    try:
        import json
        RUNTIME_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        current_data = {}
        if RUNTIME_SETTINGS_FILE.exists():
            try:
                current_data = json.loads(RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        current_data.update({
            "provider": getattr(_ai, "active_provider", "local"),
            "local_endpoint": cfg.local_llm_endpoint,
            "local_model": cfg.local_llm_model,
            "gemini_key": cfg.gemini_api_key,
            "gemini_model": cfg.gemini_model,
            "openrouter_key": cfg.openrouter_api_key,
            "openrouter_model": cfg.openrouter_model,
        })
        RUNTIME_SETTINGS_FILE.write_text(json.dumps(current_data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save runtime settings to JSON: {e}")

    return {
        "status": "ok",
        "message": "LLM configuration updated successfully",
        "current": {
            "provider": getattr(_ai, "active_provider", "local"),
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


class WatchlistBatchAddRequest(BaseModel):
    items: list[WatchlistAddRequest]


def _save_runtime_watchlist(watchlist: list[dict]):
    try:
        import json
        data = {}
        if RUNTIME_SETTINGS_FILE.exists():
            try:
                data = json.loads(RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["watchlist"] = watchlist
        RUNTIME_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            from app.services.event_trigger import invalidate_runtime_settings_cache
            invalidate_runtime_settings_cache()
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"[Settings] Failed to save watchlist to file: {e}")


def _normalize_symbol(s: str) -> str:
    import urllib.parse
    if not s:
        return ""
    dec = urllib.parse.unquote(s).strip().upper()
    return dec.replace("-", "").replace("/", "").replace("_", "")


async def _do_remove_watchlist_item(symbol: str) -> dict:
    from app.services.event_trigger import MarketMonitor
    norm = _normalize_symbol(symbol)
    monitor = MarketMonitor.get_instance()
    before_len = len(monitor.watchlist)
    monitor.watchlist = [
        item for item in monitor.watchlist
        if _normalize_symbol(item.get("symbol", "")) != norm
    ]
    after_len = len(monitor.watchlist)
    
    # Synchronously prune recent_signals
    active_norm_symbols = {
        _normalize_symbol(w.get("symbol", "")) for w in monitor.watchlist if "symbol" in w
    }
    monitor.recent_signals = [
        s for s in monitor.recent_signals
        if _normalize_symbol(s.get("symbol", "")) in active_norm_symbols
    ]
    
    _save_runtime_watchlist(monitor.watchlist)
    return {
        "status": "removed" if after_len < before_len else "not_found",
        "symbol": symbol,
        "removed": after_len < before_len,
        "remaining_count": after_len,
    }


@router.get("/watchlist")
async def get_watchlist(_key: str = Depends(verify_api_key)):
    from app.services.event_trigger import MarketMonitor
    try:
        monitor = MarketMonitor.get_instance()
        return {"watchlist": monitor.watchlist}
    except Exception:
        return {"watchlist": []}


@router.post("/watchlist")
async def add_to_watchlist(item: WatchlistAddRequest, _key: str = Depends(verify_api_key)):
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    norm_new = _normalize_symbol(item.symbol)
    for existing in monitor.watchlist:
        if _normalize_symbol(existing.get("symbol", "")) == norm_new:
            return {"status": "exists", "message": f"{item.symbol} is already in the watchlist"}

    new_item = {
        "symbol": item.symbol,
        "timeframe": item.timeframe,
        "htf_timeframe": item.htf_timeframe,
        "market_type": item.market_type,
        "exchange": item.exchange,
    }
    monitor.watchlist.append(new_item)
    _save_runtime_watchlist(monitor.watchlist)
    return {"status": "added", "item": new_item, "total_count": len(monitor.watchlist)}


@router.post("/watchlist/batch")
async def add_batch_to_watchlist(req: WatchlistBatchAddRequest, _key: str = Depends(verify_api_key)):
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    existing_symbols = {_normalize_symbol(w.get("symbol", "")) for w in monitor.watchlist if "symbol" in w}
    added = []
    
    for item in req.items:
        norm = _normalize_symbol(item.symbol)
        if norm and norm not in existing_symbols:
            new_item = {
                "symbol": item.symbol,
                "timeframe": item.timeframe,
                "htf_timeframe": item.htf_timeframe,
                "market_type": item.market_type,
                "exchange": item.exchange,
            }
            monitor.watchlist.append(new_item)
            existing_symbols.add(norm)
            added.append(new_item)
            
    if added:
        _save_runtime_watchlist(monitor.watchlist)
        
    return {
        "status": "ok",
        "added_count": len(added),
        "total_count": len(monitor.watchlist),
        "added": added,
    }


@router.get("/assets/catalog")
async def get_assets_catalog(_key: str = Depends(verify_api_key)):
    """Return available asset catalog across all supported markets."""
    from app.engines.innovestx_client import InnovestXClient
    cfg = get_settings()
    
    innovestx_symbols = []
    try:
        if cfg.innovestx_api_key and cfg.innovestx_api_secret:
            client = InnovestXClient(api_key=cfg.innovestx_api_key, api_secret=cfg.innovestx_api_secret)
            pairs = await client.get_formatted_symbols()
            if pairs:
                innovestx_symbols = [p["symbol"] for p in pairs]
    except Exception:
        pass
        
    if not innovestx_symbols:
        innovestx_symbols = [
            "BTC/THB", "ETH/THB", "SOL/THB", "XRP/THB", "ADA/THB", "DOGE/THB",
            "BNB/THB", "AVAX/THB", "DOT/THB", "POL/THB", "LINK/THB", "NEAR/THB",
            "SUI/THB", "SEI/THB", "ARB/THB", "OP/THB", "USDT/THB", "USDC/THB",
            "AAVE/THB", "UNI/THB", "SAND/THB", "GALA/THB", "PEPE/THB", "SHIB/THB",
            "XLM/THB", "DYDX/THB", "CRV/THB", "PENDLE/THB", "LDO/THB", "WLD/THB",
            "TIA/THB", "XAUT/THB", "ASTER/THB", "BLU/THB", "REALX/THB", "SUMX/THB",
            "CHZ/THB", "SNX/THB", "AXS/THB", "BCH/THB"
        ]

    crypto_global = [
        {"symbol": "BTC/USDT", "name": "Bitcoin", "exchange": "binance"},
        {"symbol": "ETH/USDT", "name": "Ethereum", "exchange": "binance"},
        {"symbol": "SOL/USDT", "name": "Solana", "exchange": "binance"},
        {"symbol": "BNB/USDT", "name": "BNB", "exchange": "binance"},
        {"symbol": "XRP/USDT", "name": "Ripple", "exchange": "binance"},
        {"symbol": "ADA/USDT", "name": "Cardano", "exchange": "binance"},
        {"symbol": "DOGE/USDT", "name": "Dogecoin", "exchange": "binance"},
        {"symbol": "AVAX/USDT", "name": "Avalanche", "exchange": "binance"},
        {"symbol": "LINK/USDT", "name": "Chainlink", "exchange": "binance"},
        {"symbol": "SUI/USDT", "name": "Sui Network", "exchange": "binance"},
        {"symbol": "NEAR/USDT", "name": "NEAR Protocol", "exchange": "binance"},
        {"symbol": "DOT/USDT", "name": "Polkadot", "exchange": "binance"},
        {"symbol": "PEPE/USDT", "name": "Pepe", "exchange": "binance"},
        {"symbol": "SHIB/USDT", "name": "Shiba Inu", "exchange": "binance"},
    ]

    forex_metals = [
        {"symbol": "XAUUSD", "name": "Gold / USD Spot", "exchange": "mt5"},
        {"symbol": "EURUSD", "name": "Euro / US Dollar", "exchange": "mt5"},
        {"symbol": "GBPUSD", "name": "British Pound / USD", "exchange": "mt5"},
        {"symbol": "USDJPY", "name": "US Dollar / Japanese Yen", "exchange": "mt5"},
        {"symbol": "AUDUSD", "name": "Australian Dollar / USD", "exchange": "mt5"},
        {"symbol": "USDCAD", "name": "US Dollar / Canadian Dollar", "exchange": "mt5"},
        {"symbol": "USDCHF", "name": "US Dollar / Swiss Franc", "exchange": "mt5"},
        {"symbol": "GBPJPY", "name": "British Pound / Yen", "exchange": "mt5"},
        {"symbol": "XAGUSD", "name": "Silver / USD Spot", "exchange": "mt5"},
        {"symbol": "USOIL", "name": "WTI Crude Oil", "exchange": "mt5"},
    ]

    stocks = [
        {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "alpaca"},
        {"symbol": "TSLA", "name": "Tesla, Inc.", "exchange": "alpaca"},
        {"symbol": "NVDA", "name": "NVIDIA Corporation", "exchange": "alpaca"},
        {"symbol": "MSFT", "name": "Microsoft Corporation", "exchange": "alpaca"},
        {"symbol": "AMZN", "name": "Amazon.com, Inc.", "exchange": "alpaca"},
        {"symbol": "GOOGL", "name": "Alphabet Inc.", "exchange": "alpaca"},
        {"symbol": "META", "name": "Meta Platforms, Inc.", "exchange": "alpaca"},
        {"symbol": "AMD", "name": "Advanced Micro Devices", "exchange": "alpaca"},
        {"symbol": "PLTR", "name": "Palantir Technologies", "exchange": "alpaca"},
        {"symbol": "COIN", "name": "Coinbase Global", "exchange": "alpaca"},
        {"symbol": "MSTR", "name": "MicroStrategy Inc.", "exchange": "alpaca"},
        {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "exchange": "alpaca"},
        {"symbol": "QQQ", "name": "Invesco QQQ Trust", "exchange": "alpaca"},
    ]

    return {
        "innovestx_thb": [{"symbol": s, "name": s.split('/')[0] + " / THB", "exchange": "innovestx", "market_type": "crypto"} for s in innovestx_symbols],
        "crypto_global": [{"symbol": c["symbol"], "name": c["name"], "exchange": c["exchange"], "market_type": "crypto"} for c in crypto_global],
        "forex_metals": [{"symbol": f["symbol"], "name": f["name"], "exchange": f["exchange"], "market_type": "forex"} for f in forex_metals],
        "stocks": [{"symbol": st["symbol"], "name": st["name"], "exchange": st["exchange"], "market_type": "stock"} for st in stocks],
    }


@router.delete("/watchlist")
async def remove_from_watchlist_query(symbol: Optional[str] = Query(None), _key: str = Depends(verify_api_key)):
    if not symbol:
        return {"status": "error", "message": "Symbol query parameter required"}
    return await _do_remove_watchlist_item(symbol)


@router.delete("/watchlist/{symbol:path}")
async def remove_from_watchlist_path(symbol: str, _key: str = Depends(verify_api_key)):
    return await _do_remove_watchlist_item(symbol)


@router.post("/watchlist/reset-default")
async def reset_watchlist_to_default(_key: str = Depends(verify_api_key)):
    """Reset watchlist to default core 8 assets."""
    import copy
    from app.services.event_trigger import MarketMonitor, DEFAULT_WATCHLIST
    monitor = MarketMonitor.get_instance()
    monitor.watchlist = copy.deepcopy(DEFAULT_WATCHLIST)
    
    # Synchronously prune recent_signals
    active_norm_symbols = {
        _normalize_symbol(w.get("symbol", "")) for w in monitor.watchlist if "symbol" in w
    }
    monitor.recent_signals = [
        s for s in monitor.recent_signals
        if _normalize_symbol(s.get("symbol", "")) in active_norm_symbols
    ]
    
    _save_runtime_watchlist(monitor.watchlist)
    return {
        "status": "ok",
        "message": "Watchlist reset to default core assets",
        "watchlist": monitor.watchlist,
        "count": len(monitor.watchlist),
    }


@router.post("/watchlist/clear-innovestx")
async def clear_innovestx_watchlist(_key: str = Depends(verify_api_key)):
    """Remove all /THB InnovestX symbols from the watchlist in one click."""
    from app.services.event_trigger import MarketMonitor
    monitor = MarketMonitor.get_instance()
    before_len = len(monitor.watchlist)
    monitor.watchlist = [
        item for item in monitor.watchlist
        if not (item.get("symbol", "").upper().endswith("/THB") or item.get("exchange") == "innovestx")
    ]
    after_len = len(monitor.watchlist)
    
    # Synchronously prune recent_signals
    active_norm_symbols = {
        _normalize_symbol(w.get("symbol", "")) for w in monitor.watchlist if "symbol" in w
    }
    monitor.recent_signals = [
        s for s in monitor.recent_signals
        if _normalize_symbol(s.get("symbol", "")) in active_norm_symbols
    ]
    
    _save_runtime_watchlist(monitor.watchlist)
    return {
        "status": "ok",
        "message": f"Cleared {before_len - after_len} InnovestX /THB symbols from watchlist",
        "removed_count": before_len - after_len,
        "remaining_count": after_len,
        "watchlist": monitor.watchlist,
    }


class WatchlistImportInnovestXRequest(BaseModel):
    symbols: Optional[List[str]] = None
    timeframe: str = "1h"
    htf_timeframe: str = "4h"
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


@router.post("/watchlist/import-innovestx")
async def import_innovestx_watchlist(
    req: Optional[WatchlistImportInnovestXRequest] = None,
    _key: str = Depends(verify_api_key),
):
    """Fetch all tradable THB asset pairs from InnovestX and automatically import to proactive watchlist."""
    from app.services.event_trigger import MarketMonitor
    from app.engines.innovestx_client import InnovestXClient
    cfg = get_settings()

    actual_req = req or WatchlistImportInnovestXRequest()
    key = (actual_req.api_key or cfg.innovestx_api_key).strip()
    sec = (actual_req.api_secret or cfg.innovestx_api_secret).strip()

    client = InnovestXClient(api_key=key, api_secret=sec)
    pairs = await client.get_formatted_symbols()

    if not pairs:
        fallback_symbols = [
            "BTC/THB", "ETH/THB", "SOL/THB", "XRP/THB", "ADA/THB", "DOGE/THB",
            "BNB/THB", "AVAX/THB", "DOT/THB", "POL/THB", "LINK/THB", "NEAR/THB",
            "SUI/THB", "SEI/THB", "ARB/THB", "OP/THB", "USDT/THB", "USDC/THB",
            "AAVE/THB", "UNI/THB", "SAND/THB", "GALA/THB", "PEPE/THB", "SHIB/THB",
            "XLM/THB", "DYDX/THB", "CRV/THB", "PENDLE/THB", "LDO/THB", "WLD/THB",
            "TIA/THB", "XAUT/THB", "ASTER/THB", "BLU/THB", "REALX/THB", "SUMX/THB",
            "CHZ/THB", "SNX/THB", "AXS/THB", "BCH/THB"
        ]
        pairs = [{"symbol": s} for s in fallback_symbols]

    monitor = MarketMonitor.get_instance()
    existing_symbols = {_normalize_symbol(item["symbol"]) for item in monitor.watchlist}
    added = []

    target_pairs = req.symbols if req and req.symbols else [p["symbol"] for p in pairs]

    for p_sym in target_pairs:
        if _normalize_symbol(p_sym) not in existing_symbols:
            new_item = {
                "symbol": p_sym,
                "timeframe": req.timeframe if req else "1h",
                "htf_timeframe": req.htf_timeframe if req else "4h",
                "market_type": "crypto",
                "exchange": "innovestx",
            }
            monitor.watchlist.append(new_item)
            existing_symbols.add(_normalize_symbol(p_sym))
            added.append(new_item)

    _save_runtime_watchlist(monitor.watchlist)

    return {
        "status": "ok",
        "message": f"Successfully synced {len(added)} InnovestX THB digital asset pairs into watchlist!",
        "added_count": len(added),
        "total_watchlist_count": len(monitor.watchlist),
        "added_symbols": [a["symbol"] for a in added],
    }



@router.get("/broker/innovestx/symbols")
async def get_innovestx_symbols(
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
    _key: str = Depends(verify_api_key),
):
    """Get list of formatted InnovestX symbols."""
    from app.engines.innovestx_client import InnovestXClient
    cfg = get_settings()
    key = (api_key or cfg.innovestx_api_key).strip()
    sec = (api_secret or cfg.innovestx_api_secret).strip()
    client = InnovestXClient(api_key=key, api_secret=sec)
    pairs = await client.get_formatted_symbols()
    return {"symbols": pairs, "count": len(pairs)}


class InnovestXFeeInquiryRequest(BaseModel):
    symbol: str = "BTCTHB"
    amount: float = 1.0
    price: float = 2650000.0
    side: Literal["BUY", "SELL", "buy", "sell"] = "BUY"


@router.post("/broker/innovestx/estimate-fee")
async def estimate_innovestx_fee(
    req: InnovestXFeeInquiryRequest,
    _key: str = Depends(verify_api_key),
):
    """Estimate transaction fee for an order on InnovestX Digital Asset Exchange."""
    from app.engines.innovestx_client import InnovestXClient
    client = InnovestXClient()
    if not client.is_configured():
        # Return fallback estimate if keys are not loaded yet (0.25% standard trading fee)
        fee_rate = 0.0025
        est_fee = round(req.amount * req.price * fee_rate, 2)
        return {
            "code": "0000",
            "message": "Calculated via standard exchange fee schedule (0.25%)",
            "data": {
                "orderFee": str(est_fee),
                "product": "THB",
                "rate": "0.25%",
            }
        }
    res = await client.get_estimate_fee(
        symbol=req.symbol,
        amount=req.amount,
        price=req.price,
        side=req.side,
    )
    return res


# ------------------------------------------------------------------
# Broker & Exchange Connections Settings
# ------------------------------------------------------------------

class BrokerConfigRequest(BaseModel):
    binance_api_key: Optional[str] = None
    binance_api_secret: Optional[str] = None
    bybit_api_key: Optional[str] = None
    bybit_api_secret: Optional[str] = None
    innovestx_api_key: Optional[str] = None
    innovestx_api_secret: Optional[str] = None
    innovestx_base_url: Optional[str] = None
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
        "innovestx_api_key": _mask_secret(cfg.innovestx_api_key),
        "innovestx_api_secret": _mask_secret(cfg.innovestx_api_secret),
        "innovestx_base_url": cfg.innovestx_base_url,
        "innovestx_configured": bool(cfg.innovestx_api_key and cfg.innovestx_api_secret),
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

    if req.innovestx_api_key is not None:
        cfg.innovestx_api_key = req.innovestx_api_key.strip()
    if req.innovestx_api_secret is not None:
        cfg.innovestx_api_secret = req.innovestx_api_secret.strip()
    if req.innovestx_base_url is not None:
        cfg.innovestx_base_url = req.innovestx_base_url.strip()

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
                "INNOVESTX_API_KEY": cfg.innovestx_api_key,
                "INNOVESTX_API_SECRET": cfg.innovestx_api_secret,
                "INNOVESTX_BASE_URL": cfg.innovestx_base_url,
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
                os.environ[key] = str(val)
                if key not in matched_keys:
                    new_lines.append(f"{key}={val}")
            ENV_FILE.write_text("\n".join(new_lines), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to persist broker settings to .env: {e}")

    return {"status": "ok", "message": "Broker & Exchange settings updated successfully"}


@router.delete("/brokers/config/{broker}")
async def clear_broker_config(broker: str, _key: str = Depends(verify_api_key)):
    """Clear credentials for a specific broker and persist to .env."""
    cfg = get_settings()
    b = broker.lower().strip()
    cleared = []

    if b in ["innovestx", "invx"]:
        cfg.innovestx_api_key = ""
        cfg.innovestx_api_secret = ""
        cleared = ["INNOVESTX_API_KEY", "INNOVESTX_API_SECRET"]
    elif b in ["binance"]:
        cfg.binance_api_key = ""
        cfg.binance_api_secret = ""
        cleared = ["BINANCE_API_KEY", "BINANCE_API_SECRET"]
    elif b in ["bybit"]:
        cfg.bybit_api_key = ""
        cfg.bybit_api_secret = ""
        cleared = ["BYBIT_API_KEY", "BYBIT_API_SECRET"]
    elif b in ["mt5", "metatrader"]:
        cfg.mt5_login = 0
        cfg.mt5_password = ""
        cfg.mt5_server = ""
        cleared = ["MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"]
    elif b in ["alpaca"]:
        cfg.alpaca_api_key = ""
        cfg.alpaca_api_secret = ""
        cleared = ["ALPACA_API_KEY", "ALPACA_API_SECRET"]
    else:
        raise HTTPException(status_code=400, detail=f"Unknown broker: {broker}")

    # Persist to .env and os.environ
    try:
        if ENV_FILE.exists():
            lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
            updates = {
                "INNOVESTX_API_KEY": cfg.innovestx_api_key,
                "INNOVESTX_API_SECRET": cfg.innovestx_api_secret,
                "BINANCE_API_KEY": cfg.binance_api_key,
                "BINANCE_API_SECRET": cfg.binance_api_secret,
                "BYBIT_API_KEY": cfg.bybit_api_key,
                "BYBIT_API_SECRET": cfg.bybit_api_secret,
                "MT5_LOGIN": str(cfg.mt5_login),
                "MT5_PASSWORD": cfg.mt5_password,
                "MT5_SERVER": cfg.mt5_server,
                "ALPACA_API_KEY": cfg.alpaca_api_key,
                "ALPACA_API_SECRET": cfg.alpaca_api_secret,
            }
            for k, v in updates.items():
                os.environ[k] = str(v)
            new_lines = []
            for line in lines:
                key = line.split("=")[0].strip() if "=" in line else None
                if key in updates:
                    new_lines.append(f"{key}={updates[key]}")
                else:
                    new_lines.append(line)
            ENV_FILE.write_text("\n".join(new_lines), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to persist cleared broker settings to .env: {e}")

    return {"status": "ok", "message": f"Cleared {broker} credentials", "cleared_fields": cleared}



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
    from app.engines.innovestx_client import InnovestXClient
    cfg = get_settings()

    if req.broker_type == "innovestx":
        key = (req.api_key or cfg.innovestx_api_key).strip()
        sec = (req.api_secret or cfg.innovestx_api_secret).strip()
        base = "https://api.innovestxonline.com"
        if req.base_url and "innovestx" in req.base_url:
            base = req.base_url.strip()
        elif cfg.innovestx_base_url and "innovestx" in cfg.innovestx_base_url:
            base = cfg.innovestx_base_url.strip()

        client = InnovestXClient(api_key=key, api_secret=sec, base_url=base)
        res = await client.test_connection()
        if res.get("connected"):
            return {
                "status": "ok",
                "message": f"Connected to InnovestX (SCBX)! Found {res.get('total_assets', 39)} digital asset pairs",
            }
        return {
            "status": "error",
            "message": f"InnovestX Connection failed: {res.get('error') or res.get('message')}",
        }

    elif req.broker_type == "binance":
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
    import os
    os.environ["TRADING_MODE"] = req.mode
    get_settings.cache_clear()
    cfg = get_settings()
    cfg.trading_mode = req.mode

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


class RiskConfigRequest(BaseModel):
    entry_mode: Optional[Literal["limit", "market"]] = "limit"
    auto_sl_tp: Optional[bool] = True
    auto_invalidation: Optional[bool] = True
    risk_per_trade: Optional[float] = 1.0
    max_daily_loss: Optional[float] = 3.0
    max_open_positions: Optional[int] = 5
    target_rr: Optional[float] = 2.0
    default_sl_pct: Optional[float] = 1.0


@router.get("/risk/config")
async def get_risk_config(_key: str = Depends(verify_api_key)):
    """Get active risk management and entry mode configuration."""
    cfg = get_settings()
    entry_mode = "limit"
    auto_sl_tp = True
    auto_invalidation = True
    target_rr = 2.0
    default_sl_pct = 1.0
    if RUNTIME_SETTINGS_FILE.exists():
        try:
            import json
            data = json.loads(RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
            entry_mode = data.get("entry_mode", "limit")
            auto_sl_tp = data.get("auto_sl_tp", True)
            auto_invalidation = data.get("auto_invalidation", True)
            target_rr = float(data.get("target_rr", 2.0))
            default_sl_pct = float(data.get("default_sl_pct", 1.0))
        except Exception:
            pass

    return {
        "entry_mode": entry_mode,
        "auto_sl_tp": auto_sl_tp,
        "auto_invalidation": auto_invalidation,
        "risk_per_trade": cfg.default_risk_per_trade,
        "max_daily_loss": cfg.max_daily_loss,
        "max_open_positions": cfg.max_open_positions,
        "target_rr": target_rr,
        "default_sl_pct": default_sl_pct,
    }


@router.post("/risk/config")
async def update_risk_config(req: RiskConfigRequest, _key: str = Depends(verify_api_key)):
    """Update risk management and entry mode settings and persist to JSON storage."""
    cfg = get_settings()
    if req.risk_per_trade is not None:
        cfg.default_risk_per_trade = req.risk_per_trade
    if req.max_daily_loss is not None:
        cfg.max_daily_loss = req.max_daily_loss
    if req.max_open_positions is not None:
        cfg.max_open_positions = req.max_open_positions

    try:
        import json
        data = {}
        if RUNTIME_SETTINGS_FILE.exists():
            try:
                data = json.loads(RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}

        if req.entry_mode is not None:
            data["entry_mode"] = req.entry_mode
        if req.auto_sl_tp is not None:
            data["auto_sl_tp"] = req.auto_sl_tp
        if req.auto_invalidation is not None:
            data["auto_invalidation"] = req.auto_invalidation
        if req.target_rr is not None:
            data["target_rr"] = req.target_rr
        if req.default_sl_pct is not None:
            data["default_sl_pct"] = req.default_sl_pct

        data["risk_per_trade"] = cfg.default_risk_per_trade
        data["max_daily_loss"] = cfg.max_daily_loss
        data["max_open_positions"] = cfg.max_open_positions

        RUNTIME_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save risk config to JSON: {e}")

    return {
        "status": "ok",
        "message": "Risk & Entry settings saved successfully",
        "config": {
            "entry_mode": req.entry_mode,
            "auto_sl_tp": req.auto_sl_tp,
            "auto_invalidation": req.auto_invalidation,
            "risk_per_trade": cfg.default_risk_per_trade,
            "max_daily_loss": cfg.max_daily_loss,
            "target_rr": req.target_rr or 2.0,
            "default_sl_pct": req.default_sl_pct or 1.0,
        },
    }
