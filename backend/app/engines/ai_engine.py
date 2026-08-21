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
        if self.reasoning:
            import re
            msg = self.reasoning.strip()
            if "{" in msg and "reasoning" in msg:
                try:
                    match = re.search(r"\{[\s\S]*\}", msg)
                    if match:
                        p = json.loads(match.group(0))
                        if isinstance(p, dict) and p.get("reasoning"):
                            return str(p["reasoning"])
                except Exception:
                    pass
            msg = re.sub(r"^```(?:json)?\s*|\s*```$", "", msg, flags=re.MULTILINE).strip()
            return msg
        return self.risk_notes or "โครงสร้างตลาดได้รับการยืนยันตามระบบ SMC"

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
            sym = context.get('symbol', 'BTC/USDT')
            tf = context.get('timeframe', '1h')
            conf = context.get('confluence', 0)
            
            # If confluence is missing or 0, retrieve from proactive monitor
            if not conf or conf == 0:
                try:
                    from app.services.event_trigger import MarketMonitor
                    monitor = MarketMonitor.get_instance()
                    for s in monitor.recent_signals:
                        if s.get("symbol") == sym:
                            conf = s.get("confluence", conf)
                            break
                except Exception:
                    pass

            ctx_prompt = (
                f"\n[Real-Time Market Context]\n"
                f"- Current Asset: {sym}\n"
                f"- Current Price: ${context.get('price', 0):,.2f}\n"
                f"- Timeframe: {tf}\n"
                f"- SMC Bias: {context.get('bias', 'neutral')}\n"
                f"- Confluence Score: {conf}/100\n"
                f"- Open Positions: {context.get('open_positions', 0)}\n"
            )

        full_messages = []
        has_system = any(m.get("role") == "system" for m in messages)
        if not has_system:
            full_messages.append({
                "role": "system",
                "content": (
                    f"{self.system_prompt}\n\n"
                    f"ตอบคำถามผู้ใช้เป็นภาษาไทยอย่างกระชับ ตรงประเด็น ใช้หลักการ Smart Money Concepts (SMC), Order Block, FVG, Discount/Premium Zone และการคุมความเสี่ยงตามกฎข้างต้นเสมอ\n"
                    f"{ctx_prompt}"
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

        # Fallback if external LLM fails
        sym = context.get('symbol', 'Asset') if context else 'Asset'
        price = context.get('price', 0.0) if context else 0.0
        bias = str(context.get('bias', 'NEUTRAL')).upper() if context else 'NEUTRAL'
        price_str = f" (${price:,.2f})" if price > 0 else ""
        return (
            f"⚠️ **Apex AI Notice (Offline / LLM Provider Unavailable)**\n\n"
            f"ขณะนี้การเชื่อมต่อไปยัง AI Model Provider ไม่พร้อมใช้งานชั่วคราว\n"
            f"• สินทรัพย์: **{sym}**{price_str}\n"
            f"• Market Structure Bias: **{bias}**\n\n"
            f"💡 กรุณาตรวจสอบการตั้งค่า API Key หรือสถานะของ Local LLM / Gemini / OpenRouter ในเมนู Settings ครับ"
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
            prompt_path = (PROMPTS_DIR / prompt_name).resolve()
            if prompt_path.is_relative_to(PROMPTS_DIR.resolve()) and prompt_path.exists():
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
        cfg = get_settings()
        return await self._call_local_custom(messages, cfg.local_llm_endpoint, cfg.local_llm_model)

    async def _call_local_custom(self, messages: list[dict], endpoint: str, model: str) -> str:
        host_url = endpoint.rstrip("/")
        if not host_url:
            host_url = "http://host.docker.internal:11434"

        # Determine exact api_url just like ai_analyzer
        is_ollama_native = False
        api_url = f"{host_url}/chat/completions"
        if ("/v1" not in host_url and ":11434" in host_url) or host_url.endswith(":11434"):
            api_url = f"{host_url}/api/chat"
            is_ollama_native = True
        elif not host_url.endswith("/v1") and ":1234" in host_url:
            api_url = f"{host_url}/v1/chat/completions"

        target_model = model.strip() if model else ""
        if not target_model:
            target_model = "gpt-oss:120b-cloud" if is_ollama_native else "google/gemma-4-12b-qat"

        if is_ollama_native:
            payload = {
                "model": target_model,
                "messages": messages,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 768,
                },
                "stream": False,
            }
        else:
            payload = {
                "model": target_model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 1024,
                "stream": False,
            }

        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(api_url, json=payload)
            if r.status_code == 200:
                resp_data = r.json()
                if is_ollama_native:
                    msg = resp_data.get("message", {})
                    content = msg.get("content", "")
                    # Thinking models (e.g. gpt-oss:120b-cloud) put reply in 'thinking' when content is empty
                    if not content:
                        content = msg.get("thinking", "")
                    if content:
                        return content
                else:
                    choices = resp_data.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        content = msg.get("content", "")
                        if not content:
                            content = msg.get("thinking", "")
                        if content:
                            return content
            elif r.status_code == 404 and is_ollama_native:
                # If /api/chat gave 404, fallback to /v1/chat/completions
                fallback_url = f"{host_url}/v1/chat/completions"
                r2 = await client.post(fallback_url, json=payload)
                if r2.status_code == 200:
                    choices = r2.json().get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        if content:
                            return content
                raise ValueError(f"Ollama returned 404 on model '{target_model}'. Status {r.status_code}")
            else:
                raise ValueError(f"AI Provider error (Status {r.status_code}): {r.text}")

        raise ValueError(f"Could not get response from Local LLM at {api_url}")

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def _call_gemini(self, messages: list[dict]) -> str:
        return await self._call_gemini_custom(messages, self.cfg.gemini_api_key, self.cfg.gemini_model)

    async def _call_gemini_custom(self, messages: list[dict], api_key: str, model: str) -> str:
        if not api_key:
            raise ValueError("GEMINI_API_KEY not configured")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"x-goog-api-key": api_key.strip()}
        system_text = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_parts = [{"text": m["content"]} for m in messages if m["role"] != "system"]
        payload: dict[str, Any] = {"contents": [{"role": "user", "parts": user_parts}]}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=headers, json=payload)
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
            f"**Current Price**: {sig['current_price']} | **Entry Type**: {sig.get('entry_type', 'limit')}",
            f"**HTF Bias**: {sig['htf_bias']} | **LTF Bias**: {sig['bias']}",
            f"- BOS: {'✅' if sig['bos'] else '❌'} | CHoCH: {'✅' if sig['choch'] else '❌'}",
            f"- Liquidity Swept: {'✅' if sig['liquidity_swept'] else '❌'} ({sig['sweep_direction']})",
            f"- In Premium: {sig['in_premium']} | In Discount: {sig['in_discount']}",
            f"- Equilibrium: {sig['equilibrium']}",
            f"- Squeeze Status: {sig.get('squeeze_status', 'no_squeeze')} | Momentum: {sig.get('squeeze_momentum', 0.0)} ({sig.get('momentum_direction', '')})",
            f"- Volume Delta: {sig.get('volume_delta', 0.0)} (Ratio: {sig.get('delta_ratio', 0.0)}) | Absorption: {'✅' if sig.get('delta_absorption') else '❌'} ({sig.get('delta_status', '')})",
            f"- Volume Spike: {'✅' if sig.get('volume_spike') else '❌'}",
            f"- Confluence Score: {sig.get('confluence_score', sig.get('confluence', 0))}/100",
        ]
        return "\n".join(lines)

    def _parse_response(self, raw: str) -> AIAnalysis:
        import re
        text = raw.strip()
        data = None

        # 1. Try finding complete JSON block {...}
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                pass

        # 2. If complete JSON failed (e.g. truncated JSON), extract fields via regex!
        if not data:
            data = {}
            rec_m = re.search(r'"recommendation"\s*:\s*"([^"]+)"', text)
            if rec_m:
                data["recommendation"] = rec_m.group(1)
            
            conf_m = re.search(r'"confidence"\s*:\s*([0-9.]+)', text)
            if conf_m:
                data["confidence"] = conf_m.group(1)
            
            reas_m = re.search(r'"reasoning"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            if reas_m:
                try:
                    data["reasoning"] = json.loads(f'"{reas_m.group(1)}"')
                except Exception:
                    data["reasoning"] = reas_m.group(1).replace(r'\"', '"').replace(r'\n', '\n')

        if not data.get("reasoning"):
            clean = text
            if clean.startswith("```json"):
                clean = clean[7:]
            elif clean.startswith("```"):
                clean = clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
            try:
                parsed = json.loads(clean)
                if isinstance(parsed, dict):
                    data.update(parsed)
            except Exception:
                clean_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
                data["reasoning"] = clean_text

        # Map recommendation safely
        rec_raw = str(data.get("recommendation", "wait")).lower()
        if "strong_buy" in rec_raw or "strong buy" in rec_raw:
            rec = "strong_buy"
        elif "buy" in rec_raw or "long" in rec_raw:
            rec = "buy"
        elif "strong_sell" in rec_raw or "strong sell" in rec_raw:
            rec = "strong_sell"
        elif "sell" in rec_raw or "short" in rec_raw:
            rec = "sell"
        else:
            rec = "wait"

        # Map confidence (handles float 0.28 -> 28, string "85", int 85)
        conf_raw = data.get("confidence", 50)
        try:
            conf_val = float(conf_raw)
            if 0 < conf_val <= 1.0:
                conf = int(conf_val * 100)
            else:
                conf = int(conf_val)
        except Exception:
            conf = 50

        reasoning = data.get("reasoning", "")
        if not reasoning:
            reasoning = text

        key_pts = data.get("key_points", [])
        if isinstance(key_pts, str):
            key_pts = [key_pts]

        risk = data.get("risk_notes", "")
        if isinstance(risk, list):
            risk = "; ".join(str(r) for r in risk)

        return AIAnalysis(
            recommendation=rec,
            confidence=conf,
            reasoning=str(reasoning),
            key_points=key_pts,
            risk_notes=str(risk),
            market_context=str(data.get("market_context", "")),
        )
