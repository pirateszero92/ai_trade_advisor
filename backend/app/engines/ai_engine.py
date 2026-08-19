"""
AI Engine
Multi-provider LLM integration with automatic fallback chain.
Providers: Local (LM Studio / OpenAI-compat) -> Gemini -> OpenRouter
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.engines.smc_engine import SMCSignal

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
FALLBACK_CHAIN: list[str] = ["local", "gemini", "openrouter"]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AIAnalysis:
    """Result of AI analysis of a trade signal."""
    provider: str = ""
    recommendation: Literal["strong_buy", "buy", "wait", "sell", "strong_sell"] = "wait"
    confidence: int = 0            # 0-100
    reasoning: str = ""
    key_points: list[str] = field(default_factory=list)
    risk_notes: str = ""
    market_context: str = ""
    raw_response: str = ""

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "key_points": self.key_points,
            "risk_notes": self.risk_notes,
            "market_context": self.market_context,
        }


# ---------------------------------------------------------------------------
# AI Engine
# ---------------------------------------------------------------------------

class AIEngine:
    """
    Multi-provider LLM engine with graceful fallback.

    Tries each provider in ``FALLBACK_CHAIN`` until one succeeds.
    Supports SMC signal analysis and free-form chat.
    """

    def __init__(self):
        self.cfg = get_settings()
        self._system_prompt: Optional[str] = None
        self._active_prompt_file: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(
        self,
        signal: SMCSignal,
        portfolio_state: Optional[dict] = None,
        market_context: Optional[str] = None,
    ) -> AIAnalysis:
        """
        Analyse an SMC signal using the LLM chain.

        Parameters
        ----------
        signal:
            Populated SMCSignal from the SMC engine.
        portfolio_state:
            Optional dict with keys: balance, open_positions, daily_pnl_pct,
            drawdown_pct.
        market_context:
            Optional free-text market context string (news, macro, etc.).

        Returns
        -------
        AIAnalysis
        """
        context_msg = self._build_context_message(signal, portfolio_state, market_context)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": context_msg},
        ]

        for provider in FALLBACK_CHAIN:
            try:
                raw = await self._dispatch(provider, messages)
                analysis = self._parse_response(raw)
                analysis.provider = provider
                analysis.raw_response = raw
                logger.info(
                    f"[AI] {provider} -> {analysis.recommendation} "
                    f"(confidence={analysis.confidence})"
                )
                return analysis
            except Exception as exc:
                logger.warning(f"[AI] Provider {provider} failed: {exc}")

        logger.error("[AI] All providers failed — returning neutral analysis")
        return AIAnalysis(provider="none", recommendation="wait", reasoning="All LLM providers unavailable")

    async def chat(self, messages: list[dict]) -> str:
        """
        Free-form chat with the LLM chain.

        Parameters
        ----------
        messages:
            List of OpenAI-format message dicts (role/content).

        Returns
        -------
        str
            Assistant response text.
        """
        for provider in FALLBACK_CHAIN:
            try:
                return await self._dispatch(provider, messages)
            except Exception as exc:
                logger.warning(f"[AI] Chat provider {provider} failed: {exc}")
        return "Sorry, all AI providers are currently unavailable."

    async def test_connection(self, provider: str) -> dict:
        """
        Test connectivity to a specific LLM provider.

        Returns
        -------
        dict with keys: provider, ok, latency_ms, model, error.
        """
        import time
        t0 = time.perf_counter()
        try:
            test_messages = [
                {"role": "user", "content": "Reply with exactly: OK"}
            ]
            response = await self._dispatch(provider, test_messages)
            latency = int((time.perf_counter() - t0) * 1000)
            return {"provider": provider, "ok": True, "latency_ms": latency, "model": self._model_for(provider)}
        except Exception as exc:
            latency = int((time.perf_counter() - t0) * 1000)
            return {"provider": provider, "ok": False, "latency_ms": latency, "error": str(exc)}

    # ------------------------------------------------------------------
    # Prompt management
    # ------------------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        """Load and cache the active system prompt."""
        if self._system_prompt is None:
            self._load_prompt()
        return self._system_prompt or "You are an AI trade advisor."

    def reload_prompt(self) -> str:
        """Force reload the prompt from disk."""
        self._system_prompt = None
        self._active_prompt_file = None
        return self.system_prompt

    def _load_prompt(self) -> None:
        active_file = PROMPTS_DIR / "active_prompt.txt"
        try:
            prompt_name = active_file.read_text(encoding="utf-8").strip()
            prompt_path = PROMPTS_DIR / prompt_name
            if prompt_path.exists():
                self._system_prompt = prompt_path.read_text(encoding="utf-8")
                self._active_prompt_file = prompt_name
                logger.info(f"[AI] Loaded prompt: {prompt_name}")
            else:
                logger.warning(f"[AI] Prompt file not found: {prompt_path}")
                self._system_prompt = self._default_prompt()
        except Exception as exc:
            logger.warning(f"[AI] Could not load prompt: {exc}")
            self._system_prompt = self._default_prompt()

    @staticmethod
    def _default_prompt() -> str:
        return (
            "You are an expert AI trade advisor specialising in Smart Money Concepts (SMC). "
            "Analyse trade signals and respond in JSON with keys: "
            "recommendation, confidence, reasoning, key_points, risk_notes, market_context."
        )

    # ------------------------------------------------------------------
    # Provider dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, provider: str, messages: list[dict]) -> str:
        if provider == "local":
            return await self._call_local(messages)
        elif provider == "gemini":
            return await self._call_gemini(messages)
        elif provider == "openrouter":
            return await self._call_openrouter(messages)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def _call_local(self, messages: list[dict]) -> str:
        """Call a local OpenAI-compatible endpoint (e.g. LM Studio)."""
        url = f"{self.cfg.local_llm_endpoint}/chat/completions"
        payload = {
            "model": self.cfg.local_llm_model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 1500,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def _call_gemini(self, messages: list[dict]) -> str:
        """Call Google Gemini via REST API."""
        if not self.cfg.gemini_api_key:
            raise ValueError("GEMINI_API_KEY not configured")

        model = self.cfg.gemini_model
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={self.cfg.gemini_api_key}"
        )

        # Convert OpenAI messages to Gemini format
        system_text = next(
            (m["content"] for m in messages if m["role"] == "system"), ""
        )
        user_parts = [
            {"text": m["content"]} for m in messages if m["role"] != "system"
        ]

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": user_parts}],
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def _call_openrouter(self, messages: list[dict]) -> str:
        """Call OpenRouter (Claude / GPT-4o / etc.)."""
        if not self.cfg.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY not configured")

        payload = {
            "model": self.cfg.openrouter_model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 1500,
        }
        headers = {
            "Authorization": f"Bearer {self.cfg.openrouter_api_key}",
            "HTTP-Referer": "https://ai-trade-advisor",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    def _build_context_message(
        self,
        signal: SMCSignal,
        portfolio_state: Optional[dict],
        market_context: Optional[str],
    ) -> str:
        """Format signal and context into a structured prompt string."""
        sig = signal.to_dict()
        lines = [
            f"## Trade Signal Analysis Request",
            f"",
            f"**Symbol**: {sig['symbol']}  |  **Timeframe**: {sig['timeframe']}",
            f"**Current Price**: {sig['current_price']}",
            f"**HTF Bias**: {sig['htf_bias']}  |  **LTF Bias**: {sig['bias']}",
            f"",
            f"### Market Structure",
            f"- BOS: {'✅' if sig['bos'] else '❌'}  |  CHoCH: {'✅' if sig['choch'] else '❌'}",
            f"- Liquidity Swept: {'✅' if sig['liquidity_swept'] else '❌'} ({sig['sweep_direction']})",
            f"- In Premium: {sig['in_premium']}  |  In Discount: {sig['in_discount']}",
            f"- Equilibrium: {sig['equilibrium']}",
            f"",
            f"### Key Zones",
        ]

        if sig["order_block"]:
            ob = sig["order_block"]
            lines.append(f"- Order Block ({ob['direction']}): {ob['bottom']} – {ob['top']}")
        else:
            lines.append("- Order Block: None detected")

        if sig["fvg"]:
            fvg = sig["fvg"]
            lines.append(f"- FVG ({fvg['direction']}): {fvg['bottom']} – {fvg['top']}")
        else:
            lines.append("- FVG: None detected")

        if sig["equal_highs"]:
            lines.append(f"- Equal Highs (sell-side liq): {sig['equal_highs']}")
        if sig["equal_lows"]:
            lines.append(f"- Equal Lows (buy-side liq): {sig['equal_lows']}")

        lines += [
            f"",
            f"### Proposed Setup",
            f"- Direction: {sig['direction'].upper()}",
            f"- Entry: {sig['entry']}  |  SL: {sig['stop_loss']}  |  TP: {sig['take_profit']}",
            f"- Risk:Reward = {sig['risk_reward']}",
            f"- SMC Confluence Score: {sig['confluence']}/10",
        ]

        if portfolio_state:
            lines += [
                f"",
                f"### Portfolio State",
                f"- Balance: ${portfolio_state.get('balance', 'N/A')}",
                f"- Open Positions: {portfolio_state.get('open_positions', 0)}",
                f"- Daily P&L: {portfolio_state.get('daily_pnl_pct', 0):.2f}%",
                f"- Drawdown: {portfolio_state.get('drawdown_pct', 0):.2f}%",
            ]

        if market_context:
            lines += [f"", f"### Market Context", market_context]

        lines += [
            f"",
            f"---",
            f"Respond in **JSON** with these exact keys:",
            f"recommendation (strong_buy|buy|wait|sell|strong_sell), confidence (0-100),",
            f"reasoning (string), key_points (list of strings), risk_notes (string),",
            f"market_context (string).",
        ]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str) -> AIAnalysis:
        """Parse JSON from LLM response, with fallback for markdown code blocks."""
        text = raw.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON object
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    data = json.loads(text[start:end])
                except json.JSONDecodeError:
                    return AIAnalysis(reasoning=raw, recommendation="wait")
            else:
                return AIAnalysis(reasoning=raw, recommendation="wait")

        valid_recs = {"strong_buy", "buy", "wait", "sell", "strong_sell"}
        rec = data.get("recommendation", "wait").lower()
        if rec not in valid_recs:
            rec = "wait"

        return AIAnalysis(
            recommendation=rec,
            confidence=int(data.get("confidence", 0)),
            reasoning=data.get("reasoning", ""),
            key_points=data.get("key_points", []),
            risk_notes=data.get("risk_notes", ""),
            market_context=data.get("market_context", ""),
        )

    def _model_for(self, provider: str) -> str:
        if provider == "local":
            return self.cfg.local_llm_model
        elif provider == "gemini":
            return self.cfg.gemini_model
        elif provider == "openrouter":
            return self.cfg.openrouter_model
        return "unknown"
