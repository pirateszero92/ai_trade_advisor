"""Canonical single-timeframe decision snapshot used by every trading path."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

import pandas as pd
from loguru import logger

from app.core.strategy_config_store import read_strategy_config
from app.core.runtime_config import load_runtime_config
from app.core.config import get_settings
from app.engines.indicator_core import load_indicator_core_config
from app.engines.market_data import MarketDataEngine
from app.engines.regime_engine import load_regime_policy_config
from app.engines.smc_engine import SMCEngine, SMCSignal
from app.engines.strategy_engine import StrategyEngine, StrategyResult
from app.engines.timeframe_profiles import (
    load_timeframe_profiles,
    resolve_profile_indicator_config,
    validate_timeframe_profiles,
)
from app.services.analysis_snapshot import _frame_digest, _next_refresh_at


STRATEGY_FILE = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"


def execution_entry_mode() -> str:
    mode = load_runtime_config().get("entry_mode", "limit")
    return mode if mode in {"limit", "market"} else "limit"


@dataclass
class ExecutionAnalysis:
    symbol: str
    timeframe: str
    frame: pd.DataFrame
    signal: SMCSignal
    strategy: StrategyResult
    config_snapshot: dict[str, Any]
    snapshot_id: str
    generated_at: datetime
    valid_until: datetime

    def clone(self) -> ExecutionAnalysis:
        """Return an isolated working copy of the cached canonical result.

        API and background-scanner callers enrich or reject a result for their
        own presentation/execution path.  They must never mutate the object
        retained in the cache, otherwise another caller can observe those
        changes during the same closed-candle window.
        """
        return ExecutionAnalysis(
            symbol=self.symbol,
            timeframe=self.timeframe,
            frame=self.frame.copy(deep=True),
            signal=deepcopy(self.signal),
            strategy=deepcopy(self.strategy),
            config_snapshot=deepcopy(self.config_snapshot),
            snapshot_id=self.snapshot_id,
            generated_at=self.generated_at,
            valid_until=self.valid_until,
        )

    def metadata(self) -> dict[str, Any]:
        candle_digest = hashlib.sha256(_frame_digest(self.frame)).hexdigest()[:24]
        return {
            "snapshot_id": self.snapshot_id,
            "authority": "execution_timeframe_only",
            "timeframe": self.timeframe,
            "generated_at": self.generated_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "last_closed_candle": pd.Timestamp(self.frame.index[-1]).isoformat(),
            "closed_at": pd.Timestamp(self.frame.index[-1]).isoformat(),
            "candle_digest": candle_digest,
            "ai_review": deepcopy(self.signal.ai_review),
        }


def analyze_execution_frame(
    *,
    frame: pd.DataFrame,
    symbol: str,
    entry_mode: str,
    config_snapshot: dict[str, Any],
    timeframe: str | None = None,
) -> tuple[SMCSignal, StrategyResult, str]:
    """Pure closed-candle decision function shared by live and backtest paths."""
    profiles = validate_timeframe_profiles(config_snapshot.get("timeframe_profiles"))
    profile = profiles["roles"]["trigger"]
    effective_tf = timeframe or profile["timeframe"]
    indicator = resolve_profile_indicator_config(
        profile,
        config_snapshot.get("indicator_core"),
    )
    signal = SMCEngine(**profile["smc"]).analyze(
        frame.copy(),
        symbol,
        effective_tf,
        htf_bias="neutral",
        entry_mode=entry_mode,
        indicator_config=indicator,
        regime_config=config_snapshot.get("regime_policy"),
        tri_core_policy=config_snapshot.get("tri_core_policy"),
    )
    # VPIN Order Flow Toxicity
    try:
        from app.engines.vpin_engine import VPINEngine
        vpin_res = VPINEngine().calculate(frame)
        signal.order_flow = {
            "vpin": vpin_res.vpin,
            "toxic_flow_detected": vpin_res.toxic_flow_detected,
            "percentile_toxicity": vpin_res.percentile_toxicity,
            "status": vpin_res.status,
        }
    except Exception as exc:
        signal.order_flow = {"vpin": None, "toxic_flow_detected": False, "status": "unavailable"}

    strategy = StrategyEngine(strategy_config=config_snapshot).evaluate(signal)
    if strategy.effective_policy:
        signal.market_regime["effective_policy"] = strategy.effective_policy
    return signal, strategy, effective_tf


class ExecutionAnalysisService:
    def __init__(self) -> None:
        self._market = MarketDataEngine()
        self._cache: dict[tuple[str, ...], ExecutionAnalysis] = {}
        self._by_id: dict[str, ExecutionAnalysis] = {}
        self._locks: WeakValueDictionary = WeakValueDictionary()
        self._tasks: set[asyncio.Task] = set()

    @staticmethod
    def _config_snapshot() -> dict[str, Any]:
        config = read_strategy_config(STRATEGY_FILE)
        config["indicator_core"] = load_indicator_core_config()
        config["regime_policy"] = load_regime_policy_config()
        config["timeframe_profiles"] = load_timeframe_profiles()
        config["decision_authority"] = "execution_timeframe_only"
        cfg = get_settings()
        config["scanner_ai"] = {"mode": cfg.scanner_ai_mode,
                                "model": cfg.local_llm_model, "endpoint": cfg.local_llm_endpoint,
                                "provider": load_runtime_config().get("provider", "local")}
        return config

    def clear(self) -> None:
        self._cache.clear()
        self._by_id.clear()
        for task in self._tasks:
            task.cancel()

    async def close(self) -> None:
        self.clear()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _review(self, key, analysis, mode):
        from app.services.scanner_ai import scanner_ai
        try:
            reviewed = await scanner_ai.review(analysis, mode)
            cached = self._cache.get(key)
            if cached is None or cached.snapshot_id != analysis.snapshot_id:
                return  # Cache invalidated/replaced while the provider was running.
            if datetime.now(timezone.utc) >= analysis.valid_until:
                return
            # AI is an annotation on the deterministic input snapshot, not a
            # new market decision. Preserve the canonical snapshot identity.
            self._cache[key] = reviewed
            self._by_id[reviewed.snapshot_id] = reviewed
            try:
                from app.services.event_trigger import MarketMonitor, _compact_symbol
                monitor = MarketMonitor.get_instance()
                sym_norm = _compact_symbol(reviewed.symbol)
                for item in monitor.recent_signals:
                    if _compact_symbol(item.get("symbol", "")) == sym_norm:
                        item["ai_review"] = reviewed.signal.ai_review
                        break
            except Exception as exc:
                logger.debug(f"[ExecutionAnalysis] Sync to monitor failed: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scanner AI review failed")
            cached = self._cache.get(key)
            if cached is not None and cached.snapshot_id == analysis.snapshot_id:
                failed = cached.clone()
                failed.signal.ai_review = {
                    "status": "error",
                    "mode": mode,
                    "verdict": "UNAVAILABLE",
                    "advisory_only": True,
                    "error": "AI review unavailable",
                }
                failed.valid_until = datetime.now(timezone.utc) + timedelta(seconds=15)
                self._cache[key] = failed
                try:
                    from app.services.event_trigger import MarketMonitor, _compact_symbol
                    monitor = MarketMonitor.get_instance()
                    sym_norm = _compact_symbol(analysis.symbol)
                    for item in monitor.recent_signals:
                        if _compact_symbol(item.get("symbol", "")) == sym_norm:
                            item["ai_review"] = failed.signal.ai_review
                            break
                except Exception:
                    pass

    @staticmethod
    def _should_trigger_ai_review(analysis: ExecutionAnalysis) -> tuple[bool, str]:
        setup = getattr(analysis.signal, "tri_core_setup", {}) or {}
        if analysis.strategy.approved and setup.get("actionable") is True:
            return True, "deterministic_tri_core_setup"
        return False, "No deterministic Tri-Core setup; AI was not requested"

    async def get(
        self,
        *,
        symbol: str,
        market_type: str,
        exchange: str,
        entry_mode: str,
        timeframe: str | None = None,
    ) -> ExecutionAnalysis:
        config = self._config_snapshot()
        profile = validate_timeframe_profiles(config.get("timeframe_profiles"))["roles"]["trigger"]
        effective_tf = timeframe or profile["timeframe"]
        if effective_tf != profile["timeframe"]:
            raise ValueError("Requested timeframe is not the configured execution timeframe")
        config_hash = hashlib.sha256(
            json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:20]
        key = (symbol.upper(), market_type.lower(), exchange.lower(), entry_mode, effective_tf, config_hash)
        now = datetime.now(timezone.utc)
        cached = self._cache.get(key)
        if cached and now < cached.valid_until:
            return cached.clone()

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = datetime.now(timezone.utc)
            cached = self._cache.get(key)
            if cached and now < cached.valid_until:
                return cached.clone()
            frame = await self._market.get_ohlcv(
                symbol,
                effective_tf,
                market_type,
                exchange,
                limit=int(profile["lookback"]),
                closed_only=True,
            )
            if frame.empty:
                raise ValueError("No closed execution-timeframe candles available")
            signal, strategy, res_timeframe = analyze_execution_frame(
                frame=frame,
                symbol=symbol,
                entry_mode=entry_mode,
                config_snapshot=config,
                timeframe=effective_tf,
            )

            # Hourly OI/funding is research context only.  It may be attached
            # to a 15M snapshot for display/ablation, but it never causes a
            # Strategy re-evaluation and never changes snapshot identity.
            if market_type.lower() == "crypto":
                try:
                    from app.engines.sentiment_derivatives_engine import SentimentDerivativesEngine
                    p_change = 0.0
                    bars_per_hour = 4 if effective_tf == "15m" else 1
                    if len(frame) > bars_per_hour:
                        c0 = float(frame["close"].iloc[-1 - bars_per_hour])
                        c1 = float(frame["close"].iloc[-1])
                        if c0 > 0:
                            p_change = ((c1 - c0) / c0) * 100.0
                    deriv_res = await SentimentDerivativesEngine.get_sentiment(
                        symbol, market_type, exchange, price_change_pct_1h=p_change
                    )
                    signal.derivatives_sentiment = {
                        **deriv_res.to_dict(),
                        "advisory_only": True,
                        "decision_authority": False,
                        "source_timeframe": "1h",
                    }
                except Exception as exc:
                    logger.debug(f"[ExecutionAnalysis] Derivatives sentiment fetch skipped: {exc}")

            digest = hashlib.sha256()
            digest.update("|".join(key).encode("utf-8"))
            digest.update(_frame_digest(frame))
            analysis = ExecutionAnalysis(
                symbol=symbol,
                timeframe=res_timeframe,
                frame=frame.copy(),
                signal=signal,
                strategy=strategy,
                config_snapshot=deepcopy(config),
                snapshot_id=digest.hexdigest()[:24],
                generated_at=now,
                valid_until=_next_refresh_at(frame.index[-1], res_timeframe),
            )
            self._cache[key] = analysis
            self._by_id[analysis.snapshot_id] = analysis
            if len(self._by_id) > 500:
                self._by_id.pop(next(iter(self._by_id)), None)
            mode = config.get("scanner_ai", {}).get("mode", "off")
            if exchange.lower() == "innovestx":
                mode = "shadow" if mode != "off" else "off"
            if mode in {"shadow", "paper"}:
                baseline = analysis.clone()
                should_review, filter_reason = self._should_trigger_ai_review(analysis)
                if should_review:
                    analysis.signal.ai_review = {"status": "pending", "mode": mode,
                                                 "input_snapshot_id": analysis.snapshot_id,
                                                 "verdict": "PENDING", "advisory_only": True}
                    if len(self._tasks) < 64:
                        task = asyncio.create_task(self._review(key, baseline, mode))
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
                    else:
                        analysis.signal.ai_review["status"] = "queue_full"
                else:
                    analysis.signal.ai_review = {
                        "status": "not_requested",
                        "mode": mode,
                        "input_snapshot_id": analysis.snapshot_id,
                        "reason": filter_reason,
                        "advisory_only": True,
                    }
            if len(self._cache) > 500:
                self._cache.pop(next(iter(self._cache)), None)
            return analysis.clone()

    def get_by_snapshot_id(self, snapshot_id: str) -> ExecutionAnalysis | None:
        return self._by_id.get(snapshot_id)


execution_analyses = ExecutionAnalysisService()
