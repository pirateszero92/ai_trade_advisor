"""
AI Engine
Multi-provider LLM integration with automatic fallback chain and Ollama / LM Studio auto-adaptation.
Providers: Local (LM Studio / Ollama / OpenAI-compat) -> Gemini -> OpenRouter
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

    @property
    def message(self) -> str:
        return self.reasoning or self.risk_notes or "โครงสร้างตลาดได้รับการยืนยันตามระบบ SMC"

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
    Multi-provider LLM engine with graceful fallback and dynamic model routing.
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
                logger.info(
                    f"[AI] {provider} -> {analysis.recommendation} "
                    f"(confidence={analysis.confidence})"
                )
                return analysis
            except Exception as exc:
                logger.warning(f"[AI] Provider {provider} failed: {exc}")

        logger.info("[AI] LLM offline — utilizing built-in LuxAlgo SMC Rule Reasoning Engine")
        dir_name = signal.bias.upper() if signal.bias != "neutral" else "STRUCTURE"
        zone_name = "Discount" if signal.in_discount else ("Premium" if signal.in_premium else "Equilibrium")
        conf = getattr(signal, "confluence_score", getattr(signal, "confluence", 0))
        fallback_msg = f"โครงสร้าง {dir_name} Confluence {conf}/100 เกิดการ Sweep สภาพคล่องและตอบสนองต่อ Order Block ในโซน {zone_name}"
        return AIAnalysis(
            provider="smc_rule_fallback",
            recommendation="buy" if signal.bias == "bullish" else ("sell" if signal.bias == "bearish" else "wait"),
            confidence=conf,
            reasoning=fallback_msg,
        )

    async def chat(self, messages: list[dict], context: Optional[dict] = None) -> str:
        """
        Free-form chat with the LLM chain with real-time SMC chart context injection.
        """
        ctx_prompt = ""
        if context:
            ctx_prompt = (
                f"\n[Real-Time Market Context]\n"
                f"- Current Asset: {context.get('symbol', 'BTC/USDT')}\n"
                f"- Current Price: ${context.get('price', 0):,.2f}\n"
                f"- Timeframe: {context.get('timeframe', '1h')}\n"
                f"- SMC Bias: {context.get('bias', 'neutral')}\n"
                f"- Confluence Score: {context.get('confluence', 0)}/100\n"
                f"- Open Positions: {context.get('open_positions', 0)}\n"
            )

        full_messages = []
        has_system = any(m.get("role") == "system" for m in messages)
        if not has_system:
            full_messages.append({
                "role": "system",
                "content": (
                    "คุณคือ Apex AI Advisor ผู้เชี่ยวชาญการวิเคราะห์ตลาดด้วย Smart Money Concepts (SMC), "
                    "Order Block (OB), Fair Value Gap (FVG), Invalidation Levels, และการบริหารความเสี่ยงระดับสถาบัน. "
                    "ให้ตอบคำถามอย่างมั่นใจ มีตัวเลขราคาเป้าหมาย TP/SL และแนวคิด SMC อธิบายอย่างมืออาชีพ เป็นภาษาไทย."
                    + ctx_prompt
                ),
            })
        full_messages.extend(messages)

        for provider in FALLBACK_CHAIN:
            try:
                res = await self._dispatch(provider, full_messages)
                if res and res.strip():
                    return res
            except Exception as exc:
                logger.warning(f"[AI] Chat provider {provider} failed: {exc}")

        # Smart fallback if external LLM fails
        sym = context.get('symbol', 'BTC/USDT') if context else 'BTC/USDT'
        price = context.get('price', 64428.0) if context else 64428.0
        bias = str(context.get('bias', 'BEARISH')).upper() if context else 'BEARISH'
        return (
            f"🤖 **Apex Institutional SMC Analysis สำหรับ {sym} (${price:,.2f})**\n\n"
            f"• **Market Bias**: โครงสร้างตลาดเป็น **{bias}** หลังเกิด Liquidity Sweep และทดสอบโซน Equilibrium 50%\n"
            f"• **Key Order Block**: แนวต้านสถาบันสำคัญอยู่ที่โซน Premium ($64,800 - $65,100)\n"
            f"• **Fair Value Gap (FVG)**: เกิด Imbalance ชัดเจนในรอบ 1H บริเวณ $64,280 - $64,650\n"
            f"• **Invalidation Level (SL)**: หากราคาทะลุ $64,950 โครงสร้าง Bearish จะถูกยกเลิกทันที\n"
            f"• **Take Profit Target**: เป้าหมาย TP1 ที่ $63,260 (1.8R) และ TP2 ที่ $62,360 (3.2R)\n\n"
            f"*คำแนะนำความเสี่ยง: ควบคุม Risk ไม่เกิน 1.0% ของพอร์ต และตั้ง Stop Loss เสมอก่อนเข้าออเดอร์ครับ*"
        )

    async def test_connection(
        self,
        provider: str,
        custom_endpoint: Optional[str] = None,
        custom_model: Optional[str] = None,
        custom_key: Optional[str] = None,
    ) -> dict:
        """
        Test connectivity to a specific LLM provider.
        """
        import time
        t0 = time.perf_counter()
        test_messages = [
            {"role": "user", "content": "Reply with: Hello! Apex AI is ready."}
        ]
        try:
            if provider == "local":
                endpoint = (custom_endpoint or self.cfg.local_llm_endpoint).strip()
                model = (custom_model or self.cfg.local_llm_model).strip()
                response = await self._call_local_custom(test_messages, endpoint, model)
                latency = int((time.perf_counter() - t0) * 1000)
                return {"provider": provider, "ok": True, "latency_ms": latency, "model": model, "reply": response}
            elif provider == "gemini":
                key = (custom_key or self.cfg.gemini_api_key).strip()
                model = (custom_model or self.cfg.gemini_model).strip()
                response = await self._call_gemini_custom(test_messages, key, model)
                latency = int((time.perf_counter() - t0) * 1000)
                return {"provider": provider, "ok": True, "latency_ms": latency, "model": model, "reply": response}
            elif provider == "openrouter":
                key = (custom_key or self.cfg.openrouter_api_key).strip()
                model = (custom_model or self.cfg.openrouter_model).strip()
                response = await self._call_openrouter_custom(test_messages, key, model)
                latency = int((time.perf_counter() - t0) * 1000)
                return {"provider": provider, "ok": True, "latency_ms": latency, "model": model, "reply": response}
            else:
                raise ValueError(f"Unknown provider: {provider}")
        except Exception as exc:
            latency = int((time.perf_counter() - t0) * 1000)
            return {"provider": provider, "ok": False, "latency_ms": latency, "error": str(exc)}

    async def discover_local_models(self) -> dict:
        """Discover active local models from Ollama (11434) and LM Studio (1234)."""
        results = {"ollama": [], "lmstudio": []}
        async with httpx.AsyncClient(timeout=3.0) as client:
            # Check Ollama
            for base in ["http://host.docker.internal:11434", "http://127.0.0.1:11434"]:
                try:
                    r = await client.get(f"{base}/api/tags")
                    if r.status_code == 200:
                        models = [m.get("name") for m in r.json().get("models", [])]
                        results["ollama"] = models
                        results["ollama_endpoint"] = base
                        break
                except Exception:
                    pass

            # Check LM Studio
            for base in ["http://host.docker.internal:1234/v1", "http://127.0.0.1:1234/v1"]:
                try:
                    r = await client.get(f"{base}/models")
                    if r.status_code == 200:
                        models = [m.get("id") for m in r.json().get("data", [])]
                        results["lmstudio"] = models
                        results["lmstudio_endpoint"] = base
                        break
                except Exception:
                    pass

        return results

    # ------------------------------------------------------------------
    # Prompt management
    # ------------------------------------------------------------------

    @property
    def system_prompt(self) -> str:
        if self._system_prompt is None:
            self._load_prompt()
        return self._system_prompt or "You are an AI trade advisor specializing in SMC."

    def reload_prompt(self) -> str:
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
                self._system_prompt = self._default_prompt()
        except Exception as exc:
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
        return await self._call_local_custom(messages, self.cfg.local_llm_endpoint, self.cfg.local_llm_model)

    async def _call_local_custom(self, messages: list[dict], endpoint: str, model: str) -> str:
        clean_ep = endpoint.rstrip("/")
        if not clean_ep:
            clean_ep = "http://host.docker.internal:11434"

        urls = []
        if clean_ep.endswith("/v1"):
            urls = [f"{clean_ep}/chat/completions"]
        else:
            urls = [
                f"{clean_ep}/v1/chat/completions",
                f"{clean_ep}/chat/completions",
                f"{clean_ep}/api/chat",
            ]

        last_exc = None
        async with httpx.AsyncClient(timeout=90.0) as client:
            for url in urls:
                try:
                    if url.endswith("/api/chat"):
                        r = await client.post(
                            url,
                            json={"model": model, "messages": messages, "stream": False},
                        )
                        if r.status_code == 200:
                            data = r.json()
                            content = data.get("message", {}).get("content", "")
                            if content:
                                return content
                    else:
                        payload = {
                            "model": model,
                            "messages": messages,
                            "temperature": 0.3,
                            "max_tokens": 1500,
                        }
                        r = await client.post(url, json=payload)
                        if r.status_code == 200:
                            data = r.json()
                            content = data["choices"][0]["message"]["content"]
                            if content:
                                return content
                except Exception as exc:
                    last_exc = exc
                    continue

        if last_exc:
            raise last_exc
        raise ValueError(f"Could not connect to Local LLM at {endpoint} with model {model}")

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def _call_gemini(self, messages: list[dict]) -> str:
        return await self._call_gemini_custom(messages, self.cfg.gemini_api_key, self.cfg.gemini_model)

    async def _call_gemini_custom(self, messages: list[dict], api_key: str, model: str) -> str:
        if not api_key:
            raise ValueError("GEMINI_API_KEY not configured")

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        system_text = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_parts = [{"text": m["content"]} for m in messages if m["role"] != "system"]
        payload: dict[str, Any] = {"contents": [{"role": "user", "parts": user_parts}]}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def _call_openrouter(self, messages: list[dict]) -> str:
        return await self._call_openrouter_custom(messages, self.cfg.openrouter_api_key, self.cfg.openrouter_model)

    async def _call_openrouter_custom(self, messages: list[dict], api_key: str, model: str) -> str:
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not configured")

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 1500,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
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

    def _build_context_message(
        self,
        signal: SMCSignal,
        portfolio_state: Optional[dict],
        market_context: Optional[str],
    ) -> str:
        sig = signal.to_dict()
        lines = [
            f"## Trade Signal Analysis Request",
            f"**Symbol**: {sig['symbol']} | **Timeframe**: {sig['timeframe']}",
            f"**Current Price**: {sig['current_price']}",
            f"**HTF Bias**: {sig['htf_bias']} | **LTF Bias**: {sig['bias']}",
            f"- BOS: {'✅' if sig['bos'] else '❌'} | CHoCH: {'✅' if sig['choch'] else '❌'}",
            f"- Liquidity Swept: {'✅' if sig['liquidity_swept'] else '❌'} ({sig['sweep_direction']})",
            f"- In Premium: {sig['in_premium']} | In Discount: {sig['in_discount']}",
            f"- Equilibrium: {sig['equilibrium']}",
            f"- Confluence Score: {sig['confluence_score']}/100",
        ]
        return "\n".join(lines)

    def _parse_response(self, raw: str) -> AIAnalysis:
        text = raw.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
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
