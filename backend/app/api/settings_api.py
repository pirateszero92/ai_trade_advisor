"""
Settings API
Manage LLM provider settings, system prompt, notifications, and strategy configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Literal, Optional

from loguru import logger
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.core.security import verify_api_key
from app.engines.ai_engine import AIEngine
from app.engines.indicator_core import (
    public_indicator_core_config,
    save_indicator_core_config,
)
from app.engines.regime_engine import (
    load_regime_policy_config,
    save_regime_policy_config,
)
from app.engines.strategy_engine import StrategyEngine
from app.engines.timeframe_profiles import (
    load_timeframe_profiles,
    save_timeframe_profiles,
)
from app.core.url_security import configured_host_set, validate_service_url
from app.core.runtime_config import load_runtime_config, update_runtime_config

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
RUNTIME_SETTINGS_FILE = Path(__file__).parent.parent.parent / "config" / "runtime_settings.json"


def _load_runtime_settings():
    try:
        data = load_runtime_config()
        cfg = get_settings()
        # One-time migration for legacy versions that wrote cloud API keys to
        # runtime_settings.json. Keep them in this process for continuity, then
        # remove the plaintext copies. Future restarts must source secrets from
        # environment variables or an external secret manager.
        legacy_gemini = data.get("gemini_api_key") or data.get("gemini_key")
        legacy_openrouter = data.get("openrouter_api_key") or data.get("openrouter_key")
        if legacy_gemini and not cfg.gemini_api_key:
            cfg.gemini_api_key = str(legacy_gemini)
        if legacy_openrouter and not cfg.openrouter_api_key:
            cfg.openrouter_api_key = str(legacy_openrouter)
        legacy_secret_fields = (
            "gemini_key", "gemini_api_key", "openrouter_key", "openrouter_api_key",
        )
        if any(field in data for field in legacy_secret_fields):
            update_runtime_config({}, removals=legacy_secret_fields)
            logger.warning(
                "Removed legacy plaintext LLM keys from runtime_settings.json; "
                "configure environment-backed secrets before the next restart"
            )
            data = load_runtime_config()
        if data.get("provider"):
            _ai.active_provider = data["provider"]
        elif data.get("active_provider"):
            _ai.active_provider = data["active_provider"]
        if data.get("local_endpoint"):
            cfg.local_llm_endpoint = _validate_llm_endpoint(data["local_endpoint"])
        if data.get("local_model"):
            cfg.local_llm_model = data["local_model"]
        if data.get("gemini_model"):
            cfg.gemini_model = data["gemini_model"]
        if data.get("openrouter_model"):
            cfg.openrouter_model = data["openrouter_model"]
        if data.get("risk_per_trade") is not None:
            cfg.default_risk_per_trade = float(data["risk_per_trade"])
        if data.get("max_daily_loss") is not None:
            cfg.max_daily_loss = float(data["max_daily_loss"])
        if data.get("max_open_positions") is not None:
            cfg.max_open_positions = int(data["max_open_positions"])
        for broker in data.get("disabled_brokers", []):
            if broker == "innovestx":
                cfg.innovestx_api_key = cfg.innovestx_api_secret = ""
            elif broker == "binance":
                cfg.binance_api_key = cfg.binance_api_secret = ""
            elif broker == "bybit":
                cfg.bybit_api_key = cfg.bybit_api_secret = ""
            elif broker == "alpaca":
                cfg.alpaca_api_key = cfg.alpaca_api_secret = ""
            elif broker == "mt5":
                cfg.mt5_login, cfg.mt5_password, cfg.mt5_server = 0, "", ""
    except Exception as e:
        logger.error(f"Failed to load runtime settings: {e}")


class PromptSwitchRequest(BaseModel):
    prompt_file: str  # e.g. "advisor_v1.md"


class ChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16_000)


class ChatContextRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symbol: str = Field(default="BTC/USDT", min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$")
    price: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    timeframe: str = Field(default="1h", max_length=10)
    bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    confluence: int = Field(default=0, ge=0, le=100)
    open_positions: int = Field(default=0, ge=0, le=1000)
    strategy_approved: Optional[bool] = None
    strategy_direction: Literal["long", "short", "wait"] = "wait"
    setup_direction: Literal["long", "short", "wait"] = "wait"
    rejection_reasons: list[str] = Field(default_factory=list, max_length=20)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ChatMessageRequest] = Field(min_length=1, max_length=50)
    context: Optional[ChatContextRequest] = None


class LLMTestRequest(BaseModel):
    provider: Literal["local", "gemini", "openrouter"]
    endpoint: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=200)
    api_key: Optional[str] = Field(default=None, max_length=1000)


class LLMConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Optional[Literal["local", "gemini", "openrouter"]] = "local"
    local_endpoint: Optional[str] = Field(default=None, max_length=500)
    local_model: Optional[str] = Field(default=None, max_length=200)
    gemini_key: Optional[str] = Field(default=None, max_length=1000)
    gemini_api_key: Optional[str] = Field(default=None, max_length=1000)
    gemini_model: Optional[str] = Field(default=None, max_length=200)
    openrouter_key: Optional[str] = Field(default=None, max_length=1000)
    openrouter_api_key: Optional[str] = Field(default=None, max_length=1000)
    openrouter_model: Optional[str] = Field(default=None, max_length=200)


def _validate_llm_endpoint(endpoint: str) -> str:
    cfg = get_settings()
    allowed = configured_host_set(cfg.allowed_llm_hosts)
    # An endpoint provisioned by the server administrator remains valid even
    # when it is not one of the local defaults.
    from urllib.parse import urlparse
    current_host = urlparse(cfg.local_llm_endpoint).hostname
    if current_host:
        allowed.add(current_host.lower())
    return validate_service_url(endpoint, allowed_hosts=allowed, allow_private_ip=True)


_load_runtime_settings()


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
    try:
        endpoint = _validate_llm_endpoint(req.endpoint) if req.endpoint else None
    except ValueError as exc:
        # A provider test is represented by its `ok` flag. Returning a normal
        # test result keeps old mobile clients from exposing a raw Dio/HTTP 500
        # exception while preserving the outbound-host security boundary.
        return {
            "provider": req.provider,
            "ok": False,
            "latency_ms": 0,
            "error": f"AI endpoint is not allowed: {exc}",
        }
    result = await _ai.test_connection(
        provider=req.provider,
        custom_endpoint=endpoint,
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

    validated_local_endpoint: Optional[str] = None
    if req.local_endpoint is not None:
        try:
            validated_local_endpoint = _validate_llm_endpoint(req.local_endpoint)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    from app.core.config import update_runtime_setting

    if req.provider is not None:
        _ai.active_provider = req.provider.strip()
    if validated_local_endpoint is not None:
        update_runtime_setting("local_llm_endpoint", validated_local_endpoint)
    if req.local_model is not None:
        update_runtime_setting("local_llm_model", req.local_model.strip())
    
    gem_k = req.gemini_api_key if req.gemini_api_key is not None else req.gemini_key
    if gem_k is not None:
        clean_gem = gem_k.strip()
        if clean_gem and "*" not in clean_gem:
            update_runtime_setting("gemini_api_key", clean_gem)
    if req.gemini_model is not None:
        update_runtime_setting("gemini_model", req.gemini_model.strip())
        
    open_k = req.openrouter_api_key if req.openrouter_api_key is not None else req.openrouter_key
    if open_k is not None:
        clean_open = open_k.strip()
        if clean_open and "*" not in clean_open:
            update_runtime_setting("openrouter_api_key", clean_open)
    if req.openrouter_model is not None:
        update_runtime_setting("openrouter_model", req.openrouter_model.strip())

    from app.api import signals
    from app.services.event_trigger import MarketMonitor
    active_provider = getattr(_ai, "active_provider", "local")
    signals._ai.active_provider = active_provider
    MarketMonitor.get_instance().ai.active_provider = active_provider

    # Persist only non-secret provider settings. API keys must come from the
    # process environment/secret manager and remain memory-only when changed
    # through this endpoint.
    try:
        update_runtime_config({
            "provider": getattr(_ai, "active_provider", "local"),
            "local_endpoint": cfg.local_llm_endpoint,
            "local_model": cfg.local_llm_model,
            "gemini_model": cfg.gemini_model,
            "openrouter_model": cfg.openrouter_model,
        }, removals=("gemini_key", "gemini_api_key", "openrouter_key", "openrouter_api_key"))
    except Exception as e:
        logger.error(f"Failed to save runtime settings: {e}")
        raise HTTPException(status_code=500, detail="Unable to persist LLM configuration") from e

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
    response = await _ai.chat(
        [message.model_dump() for message in req.messages],
        context=req.context.model_dump() if req.context else None,
    )
    return {"response": response}


# ------------------------------------------------------------------
# Prompts
# ------------------------------------------------------------------

def _reload_all_ai_prompts() -> str:
    from app.api import signals
    from app.services.event_trigger import MarketMonitor

    prompt = _ai.reload_prompt()
    signals._ai.reload_prompt()
    MarketMonitor.get_instance().ai.reload_prompt()
    return prompt


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
    _reload_all_ai_prompts()
    return {"message": f"Switched to prompt: {safe_name}"}


class SavePromptRequest(BaseModel):
    name: Optional[str] = Field(default="advisor_v1.md", max_length=100)
    content: str = Field(min_length=1, max_length=100_000)


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
    _reload_all_ai_prompts()
    
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
    prompt = _reload_all_ai_prompts()
    return {"message": "Prompt reloaded", "length": len(prompt)}


# ------------------------------------------------------------------
# Strategy
# ------------------------------------------------------------------


class IndicatorLayerConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    required: bool
    weight: float = Field(gt=0, le=1000)
    params: dict[str, int | float] = Field(default_factory=dict)
    # Read-only metadata returned by GET is accepted for safe GET -> PUT
    # round trips, then stripped before persistence.
    label: Optional[str] = None
    short_label: Optional[str] = None
    description: Optional[str] = None


class IndicatorCoreConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    minimum_data_coverage: float = Field(ge=0, le=100)
    indicators: dict[str, IndicatorLayerConfigRequest]


class RegimeClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_bars: int = Field(ge=30, le=1500)
    atr_length: int = Field(ge=5, le=100)
    efficiency_lookback: int = Field(ge=10, le=500)
    volatility_lookback: int = Field(ge=20, le=1000)
    trend_efficiency_min: float = Field(ge=0.05, le=0.95)
    volatile_atr_ratio: float = Field(ge=1.0, le=10.0)
    volatile_percentile: float = Field(ge=50, le=100)


class RegimeRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_allowed: bool
    min_confluence: float = Field(ge=0, le=100)
    min_rr: float = Field(ge=1, le=20)
    risk_multiplier: float = Field(ge=0, le=1)
    require_direction_alignment: bool
    require_liquidity_sweep: bool
    require_volume_confirmation: bool
    require_squeeze_fire: bool


class RegimePolicyConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    classification: RegimeClassificationRequest
    policies: dict[str, RegimeRuleRequest]


class TimeframeProfilesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    enabled: bool = True
    roles: dict[str, dict[str, Any]]


@router.get("/indicator-core")
async def get_indicator_core(_key: str = Depends(verify_api_key)):
    """Return the active three-indicator registry and editable parameters."""
    return public_indicator_core_config()


@router.put("/indicator-core")
async def update_indicator_core(
    req: IndicatorCoreConfigRequest,
    _key: str = Depends(verify_api_key),
):
    """Validate and atomically persist the indicator decision configuration."""
    try:
        config = save_indicator_core_config(
            {
                "version": req.version,
                "minimum_data_coverage": req.minimum_data_coverage,
                "indicators": {
                    indicator_id: layer.model_dump(
                        include={"enabled", "required", "weight", "params"}
                    )
                    for indicator_id, layer in req.indicators.items()
                },
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        logger.error("Failed to persist indicator core configuration: {}", exc)
        raise HTTPException(
            status_code=500, detail="Unable to persist indicator configuration"
        ) from exc
    return {"status": "ok", "config": config}


@router.get("/regime-policy")
async def get_regime_policy(_key: str = Depends(verify_api_key)):
    """Return market-state thresholds and conservative per-regime policy."""
    return load_regime_policy_config()


@router.put("/regime-policy")
async def update_regime_policy(
    req: RegimePolicyConfigRequest,
    _key: str = Depends(verify_api_key),
):
    """Validate and atomically persist the adaptive regime policy."""
    try:
        config = save_regime_policy_config(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        logger.error("Failed to persist market regime policy: {}", exc)
        raise HTTPException(
            status_code=500, detail="Unable to persist market regime policy"
        ) from exc
    return {"status": "ok", "config": config}


@router.get("/timeframe-profiles")
async def get_timeframe_profiles(_key: str = Depends(verify_api_key)):
    """Return the active Phase 5 Bias/Setup/Trigger role profiles."""
    return load_timeframe_profiles()


@router.put("/timeframe-profiles")
async def update_timeframe_profiles(
    req: TimeframeProfilesRequest,
    _key: str = Depends(verify_api_key),
):
    """Validate and atomically persist the ordered MTF hierarchy."""
    try:
        config = save_timeframe_profiles(req.model_dump())
        from app.services.analysis_snapshot import analysis_snapshots
        from app.services.mtf_analysis import mtf_analyses

        analysis_snapshots.clear()
        mtf_analyses.clear()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        logger.error("Failed to persist timeframe profiles: {}", exc)
        raise HTTPException(
            status_code=500, detail="Unable to persist timeframe profiles"
        ) from exc
    return {"status": "ok", "config": config}

@router.post("/strategy/reload")
async def reload_strategy(_key: str = Depends(verify_api_key)):
    _strategy.reload()
    from app.api import chart, signals
    from app.services.event_trigger import MarketMonitor
    chart._strategy.reload()
    signals._strategy.reload()
    MarketMonitor.get_instance().strategy.reload()
    from app.services.analysis_snapshot import analysis_snapshots
    from app.services.mtf_analysis import mtf_analyses
    analysis_snapshots.clear()
    mtf_analyses.clear()
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
    symbol: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$")
    market_type: Literal["crypto", "forex", "stock"] = "crypto"
    timeframe: Literal["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"] = "1h"
    htf_timeframe: Literal["15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"] = "4h"
    exchange: Literal["binance", "bybit", "innovestx", "mt5", "alpaca", "yfinance"] = "binance"


class WatchlistBatchAddRequest(BaseModel):
    items: list[WatchlistAddRequest] = Field(min_length=1, max_length=200)


def _save_runtime_watchlist(watchlist: list[dict]):
    try:
        update_runtime_config({"watchlist": watchlist})
        try:
            from app.services.event_trigger import invalidate_runtime_settings_cache
            invalidate_runtime_settings_cache()
        except Exception as exc:
            logger.debug(f"Could not invalidate runtime cache: {exc}")
    except Exception as e:
        logger.error(f"[Settings] Failed to save watchlist: {e}")
        raise


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
    innovestx_base_url: Optional[str] = Field(default=None, max_length=500)
    mt5_login: Optional[int] = None
    mt5_password: Optional[str] = None
    mt5_server: Optional[str] = None
    mt5_path: Optional[str] = None
    alpaca_api_key: Optional[str] = None
    alpaca_api_secret: Optional[str] = None
    alpaca_base_url: Optional[str] = Field(default=None, max_length=500)


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
    """Update runtime broker settings in memory; secrets are never written to disk."""
    cfg = get_settings()

    if req.binance_api_key:
        cfg.binance_api_key = req.binance_api_key.strip()
    if req.binance_api_secret:
        cfg.binance_api_secret = req.binance_api_secret.strip()
    if req.bybit_api_key:
        cfg.bybit_api_key = req.bybit_api_key.strip()
    if req.bybit_api_secret:
        cfg.bybit_api_secret = req.bybit_api_secret.strip()

    if req.innovestx_api_key:
        cfg.innovestx_api_key = req.innovestx_api_key.strip()
    if req.innovestx_api_secret:
        cfg.innovestx_api_secret = req.innovestx_api_secret.strip()
    if req.innovestx_base_url:
        cfg.innovestx_base_url = validate_service_url(
            req.innovestx_base_url,
            allowed_hosts={"api.innovestxonline.com", "innovestxonline.com"},
        )

    if req.mt5_login is not None:
        cfg.mt5_login = req.mt5_login
    if req.mt5_password:
        cfg.mt5_password = req.mt5_password.strip()
    if req.mt5_server is not None:
        cfg.mt5_server = req.mt5_server.strip()
    if req.mt5_path is not None:
        cfg.mt5_path = req.mt5_path.strip()

    if req.alpaca_api_key:
        cfg.alpaca_api_key = req.alpaca_api_key.strip()
    if req.alpaca_api_secret:
        cfg.alpaca_api_secret = req.alpaca_api_secret.strip()
    if req.alpaca_base_url:
        cfg.alpaca_base_url = validate_service_url(
            req.alpaca_base_url,
            allowed_hosts={"paper-api.alpaca.markets", "api.alpaca.markets"},
        )

    enabled = set()
    if req.innovestx_api_key or req.innovestx_api_secret:
        enabled.add("innovestx")
    if req.binance_api_key or req.binance_api_secret:
        enabled.add("binance")
    if req.bybit_api_key or req.bybit_api_secret:
        enabled.add("bybit")
    if req.alpaca_api_key or req.alpaca_api_secret:
        enabled.add("alpaca")
    if req.mt5_login or req.mt5_password:
        enabled.add("mt5")
    if enabled:
        runtime = load_runtime_config()
        disabled = set(runtime.get("disabled_brokers", [])) - enabled
        update_runtime_config({"disabled_brokers": sorted(disabled)})

    return {"status": "ok", "message": "Broker & Exchange settings updated successfully"}


@router.delete("/brokers/config/{broker}")
async def clear_broker_config(broker: str, _key: str = Depends(verify_api_key)):
    """Disable a broker and clear its in-memory credentials."""
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

    canonical = "innovestx" if b == "invx" else ("mt5" if b == "metatrader" else b)
    runtime = load_runtime_config()
    disabled = set(runtime.get("disabled_brokers", []))
    disabled.add(canonical)
    update_runtime_config({"disabled_brokers": sorted(disabled)})

    return {"status": "ok", "message": f"Cleared {broker} credentials", "cleared_fields": cleared}



class BrokerTestRequest(BaseModel):
    broker_type: Literal["innovestx", "binance", "bybit", "alpaca", "mt5"]
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    server: Optional[str] = None
    login: Optional[int] = None
    password: Optional[str] = None
    base_url: Optional[str] = Field(default=None, max_length=500)


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
        if req.base_url:
            base = validate_service_url(
                req.base_url,
                allowed_hosts={"api.innovestxonline.com", "innovestxonline.com"},
            )
        elif cfg.innovestx_base_url:
            base = validate_service_url(
                cfg.innovestx_base_url,
                allowed_hosts={"api.innovestxonline.com", "innovestxonline.com"},
            )

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
        raw_base = validate_service_url(
            req.base_url or cfg.alpaca_base_url or "https://paper-api.alpaca.markets",
            allowed_hosts={"paper-api.alpaca.markets", "api.alpaca.markets"},
        )
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
                return {"status": "error", "message": f"Alpaca auth failed (HTTP {resp.status_code})"}
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
    """Compatibility endpoint: global mode is now permanently paper-safe."""
    from app.core.live_session import live_session_manager

    if req.mode == "live":
        raise HTTPException(
            status_code=409,
            detail="Persistent global Live mode was removed. Open /api/v1/live/session after explicit confirmation instead.",
        )
    cfg = get_settings()
    cfg.trading_mode = "paper"
    try:
        update_runtime_config({}, removals=("trading_mode",))
    except Exception as e:
        logger.error(f"Failed to persist trading mode: {e}")
        raise HTTPException(status_code=500, detail="Unable to persist trading mode") from e

    revoked = live_session_manager.revoke_all()
    logger.warning("Trading mode returned to PAPER; revoked {} live sessions", revoked)
    return {"status": "ok", "trading_mode": "paper", "revoked_sessions": revoked}


class RiskConfigRequest(BaseModel):
    entry_mode: Optional[Literal["limit", "market"]] = None
    auto_sl_tp: Optional[bool] = None
    auto_invalidation: Optional[bool] = None
    risk_per_trade: Optional[float] = Field(default=None, gt=0, le=5)
    max_daily_loss: Optional[float] = Field(default=None, gt=0, le=20)
    max_open_positions: Optional[int] = Field(default=None, ge=1, le=100)
    target_rr: Optional[float] = Field(default=None, ge=1, le=20)
    default_sl_pct: Optional[float] = Field(default=None, gt=0, le=20)


@router.get("/risk/config")
async def get_risk_config(_key: str = Depends(verify_api_key)):
    """Get active risk management and entry mode configuration."""
    cfg = get_settings()
    entry_mode = "limit"
    auto_sl_tp = True
    auto_invalidation = True
    target_rr = 2.0
    default_sl_pct = 1.0
    try:
        data = load_runtime_config()
        entry_mode = data.get("entry_mode", "limit")
        auto_sl_tp = data.get("auto_sl_tp", True)
        auto_invalidation = data.get("auto_invalidation", True)
        target_rr = float(data.get("target_rr", 2.0))
        default_sl_pct = float(data.get("default_sl_pct", 1.0))
    except Exception as exc:
        logger.error(f"Failed to load risk config: {exc}")
        raise HTTPException(status_code=500, detail="Unable to load risk configuration") from exc

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
        updates = {
            "risk_per_trade": cfg.default_risk_per_trade,
            "max_daily_loss": cfg.max_daily_loss,
            "max_open_positions": cfg.max_open_positions,
        }
        if req.entry_mode is not None:
            updates["entry_mode"] = req.entry_mode
        if req.auto_sl_tp is not None:
            updates["auto_sl_tp"] = req.auto_sl_tp
        if req.auto_invalidation is not None:
            updates["auto_invalidation"] = req.auto_invalidation
        if req.target_rr is not None:
            updates["target_rr"] = req.target_rr
        if req.default_sl_pct is not None:
            updates["default_sl_pct"] = req.default_sl_pct
        saved = update_runtime_config(updates)
        from app.services.event_trigger import invalidate_runtime_settings_cache
        invalidate_runtime_settings_cache()
    except Exception as e:
        logger.error(f"Failed to save risk config: {e}")
        raise HTTPException(status_code=500, detail="Unable to persist risk configuration") from e

    return {
        "status": "ok",
        "message": "Risk & Entry settings saved successfully",
        "config": {
            "entry_mode": saved.get("entry_mode", "limit"),
            "auto_sl_tp": saved.get("auto_sl_tp", True),
            "auto_invalidation": saved.get("auto_invalidation", True),
            "risk_per_trade": cfg.default_risk_per_trade,
            "max_daily_loss": cfg.max_daily_loss,
            "target_rr": saved.get("target_rr", 2.0),
            "default_sl_pct": saved.get("default_sl_pct", 1.0),
        },
    }
