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
