"""Canonical single-timeframe decision snapshot used by every trading path."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.strategy_config_store import read_strategy_config
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

    def metadata(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "authority": "execution_timeframe_only",
            "timeframe": self.timeframe,
            "generated_at": self.generated_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "last_closed_candle": pd.Timestamp(self.frame.index[-1]).isoformat(),
        }


def analyze_execution_frame(
    *,
    frame: pd.DataFrame,
    symbol: str,
    entry_mode: str,
    config_snapshot: dict[str, Any],
) -> tuple[SMCSignal, StrategyResult, str]:
    """Pure closed-candle decision function shared by live and backtest paths."""
    profiles = validate_timeframe_profiles(config_snapshot.get("timeframe_profiles"))
    profile = profiles["roles"]["trigger"]
    indicator = resolve_profile_indicator_config(
        profile,
        config_snapshot.get("indicator_core"),
    )
    signal = SMCEngine(**profile["smc"]).analyze(
        frame.copy(),
        symbol,
        profile["timeframe"],
        htf_bias="neutral",
        entry_mode=entry_mode,
        indicator_config=indicator,
        regime_config=config_snapshot.get("regime_policy"),
    )
    strategy = StrategyEngine(strategy_config=config_snapshot).evaluate(signal)
    if strategy.effective_policy:
        signal.market_regime["effective_policy"] = strategy.effective_policy
    return signal, strategy, profile["timeframe"]


class ExecutionAnalysisService:
    def __init__(self) -> None:
        self._market = MarketDataEngine()
        self._cache: dict[tuple[str, ...], ExecutionAnalysis] = {}
        self._locks: dict[tuple[str, ...], asyncio.Lock] = {}

    @staticmethod
    def _config_snapshot() -> dict[str, Any]:
        config = read_strategy_config(STRATEGY_FILE)
        config["indicator_core"] = load_indicator_core_config()
        config["regime_policy"] = load_regime_policy_config()
        config["timeframe_profiles"] = load_timeframe_profiles()
        config["decision_authority"] = "execution_timeframe_only"
        return config

    def clear(self) -> None:
        self._cache.clear()

    async def get(
        self,
        *,
        symbol: str,
        market_type: str,
        exchange: str,
        entry_mode: str,
    ) -> ExecutionAnalysis:
        config = self._config_snapshot()
        config_hash = hashlib.sha256(
            json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:20]
        key = (symbol.upper(), market_type.lower(), exchange.lower(), entry_mode, config_hash)
        now = datetime.now(timezone.utc)
        cached = self._cache.get(key)
        if cached and now < cached.valid_until:
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = datetime.now(timezone.utc)
            cached = self._cache.get(key)
            if cached and now < cached.valid_until:
                return cached
            profile = validate_timeframe_profiles(config["timeframe_profiles"])["roles"]["trigger"]
            frame = await self._market.get_ohlcv(
                symbol,
                profile["timeframe"],
                market_type,
                exchange,
                limit=int(profile["lookback"]),
                closed_only=True,
            )
            if frame.empty:
                raise ValueError("No closed execution-timeframe candles available")
            signal, strategy, timeframe = analyze_execution_frame(
                frame=frame,
                symbol=symbol,
                entry_mode=entry_mode,
                config_snapshot=config,
            )
            digest = hashlib.sha256()
            digest.update("|".join(key).encode("utf-8"))
            digest.update(_frame_digest(frame))
            analysis = ExecutionAnalysis(
                symbol=symbol,
                timeframe=timeframe,
                frame=frame.copy(),
                signal=signal,
                strategy=strategy,
                config_snapshot=deepcopy(config),
                snapshot_id=digest.hexdigest()[:24],
                generated_at=now,
                valid_until=_next_refresh_at(frame.index[-1], timeframe),
            )
            self._cache[key] = analysis
            if len(self._cache) > 500:
                self._cache.pop(next(iter(self._cache)), None)
            return analysis


execution_analyses = ExecutionAnalysisService()
