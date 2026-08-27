"""Canonical closed-candle snapshots shared by Chart and Scanner.

The decision pipeline must not let UI routes and background scans choose their
own lookback or include an in-progress candle.  This service owns that input
boundary and returns an immutable-by-convention snapshot with a stable ID.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.strategy_config_store import read_strategy_config
from app.engines.market_data import MarketDataEngine, canonical_timeframe
from app.engines.smc_engine import SMCEngine


STRATEGY_FILE = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"
DEFAULT_ANALYSIS_PROFILE = {
    "lookback": 300,
    "htf_lookback": 120,
    "closed_candles_only": True,
}


def default_htf_timeframe(timeframe: str) -> str:
    tf = canonical_timeframe(timeframe)
    return "4h" if tf in {"1m", "2m", "3m", "5m", "15m", "30m", "1h"} else "1w"


def load_analysis_profile() -> dict[str, Any]:
    raw = read_strategy_config(STRATEGY_FILE).get("analysis", {})
    if not isinstance(raw, dict):
        raw = {}
    try:
        lookback = int(raw.get("lookback", DEFAULT_ANALYSIS_PROFILE["lookback"]))
        htf_lookback = int(raw.get("htf_lookback", DEFAULT_ANALYSIS_PROFILE["htf_lookback"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("analysis lookbacks must be integers") from exc
    if not 100 <= lookback <= 1000:
        raise ValueError("analysis.lookback must be between 100 and 1000")
    if not 60 <= htf_lookback <= 500:
        raise ValueError("analysis.htf_lookback must be between 60 and 500")
    closed_only = raw.get("closed_candles_only", True)
    if closed_only is not True:
        raise ValueError("analysis.closed_candles_only must remain true for strategy decisions")
    return {
        "lookback": lookback,
        "htf_lookback": htf_lookback,
        "closed_candles_only": True,
    }


def _frame_digest(frame: pd.DataFrame) -> bytes:
    columns = [name for name in ("open", "high", "low", "close", "volume") if name in frame]
    # Evidence serialization restores JSON numbers as floats. Normalize here
    # so a replayed frame fingerprints identically even when the provider used
    # integer volume dtype at runtime.
    normalized = frame[columns].copy()
    normalized[columns] = normalized[columns].apply(pd.to_numeric, errors="coerce").astype(float)
    return pd.util.hash_pandas_object(normalized, index=True).values.tobytes()


def _next_refresh_at(last_open: pd.Timestamp, timeframe: str) -> datetime:
    """The snapshot remains canonical until the next candle can close."""
    opened = pd.Timestamp(last_open)
    if opened.tzinfo is None:
        opened = opened.tz_localize("UTC")
    else:
        opened = opened.tz_convert("UTC")
    tf = canonical_timeframe(timeframe)
    if tf == "1M":
        refresh = opened + pd.offsets.MonthBegin(2)
    else:
        seconds = {
            "1m": 60, "2m": 120, "3m": 180, "5m": 300,
            "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
            "4h": 14400, "1d": 86400, "1w": 604800,
        }[tf]
        refresh = opened + pd.to_timedelta(seconds * 2, unit="s")
    return refresh.to_pydatetime()


@dataclass(frozen=True)
class AnalysisSnapshot:
    snapshot_id: str
    symbol: str
    timeframe: str
    htf_timeframe: str
    market_type: str
    exchange: str
    ltf: pd.DataFrame
    htf: pd.DataFrame
    htf_bias: str
    generated_at: datetime
    valid_until: datetime
    profile: dict[str, Any]

    def metadata(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "source": "shared_closed_candle_snapshot",
            "generated_at": self.generated_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "last_closed_candle": self.ltf.index[-1].isoformat(),
            "htf_last_closed_candle": self.htf.index[-1].isoformat() if not self.htf.empty else None,
            "lookback": int(self.profile["lookback"]),
            "htf_lookback": int(self.profile["htf_lookback"]),
            "candle_policy": "closed_only",
            "ltf_candles": len(self.ltf),
            "htf_candles": len(self.htf),
        }


class AnalysisSnapshotService:
    """Build and cache one canonical market window per closed candle."""

    def __init__(self) -> None:
        self._market = MarketDataEngine()
        self._smc = SMCEngine()
        self._cache: dict[tuple[str, ...], AnalysisSnapshot] = {}
        self._by_id: dict[str, AnalysisSnapshot] = {}
        self._locks: dict[tuple[str, ...], asyncio.Lock] = {}

    def clear(self) -> None:
        self._cache.clear()
        self._by_id.clear()

    async def get(
        self,
        *,
        symbol: str,
        timeframe: str,
        htf_timeframe: str | None,
        market_type: str,
        exchange: str,
    ) -> AnalysisSnapshot:
        profile = load_analysis_profile()
        tf = canonical_timeframe(timeframe)
        htf = canonical_timeframe(htf_timeframe or default_htf_timeframe(tf))
        key = (
            symbol.upper(), tf, htf, market_type.lower(), exchange.lower(),
            str(profile["lookback"]), str(profile["htf_lookback"]),
        )
        now = datetime.now(timezone.utc)
        cached = self._cache.get(key)
        if cached is not None and now < cached.valid_until:
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = datetime.now(timezone.utc)
            cached = self._cache.get(key)
            if cached is not None and now < cached.valid_until:
                return cached
            ltf, htf_frame = await asyncio.gather(
                self._market.get_ohlcv(
                    symbol, tf, market_type, exchange,
                    limit=int(profile["lookback"]), closed_only=True,
                ),
                self._market.get_ohlcv(
                    symbol, htf, market_type, exchange,
                    limit=int(profile["htf_lookback"]), closed_only=True,
                ),
            )
            if ltf.empty:
                raise ValueError("No closed LTF candles available")

            htf_bias = "neutral"
            if not htf_frame.empty:
                htf_bias = self._smc.analyze(htf_frame.copy(), symbol, htf).bias

            digest = hashlib.sha256()
            digest.update("|".join(key).encode("utf-8"))
            digest.update(_frame_digest(ltf))
            digest.update(_frame_digest(htf_frame))
            snapshot_id = digest.hexdigest()[:24]
            snapshot = AnalysisSnapshot(
                snapshot_id=snapshot_id,
                symbol=symbol,
                timeframe=tf,
                htf_timeframe=htf,
                market_type=market_type,
                exchange=exchange,
                ltf=ltf.copy(),
                htf=htf_frame.copy(),
                htf_bias=htf_bias,
                generated_at=now,
                valid_until=_next_refresh_at(ltf.index[-1], tf),
                profile=profile,
            )
            self._cache[key] = snapshot
            self._by_id[snapshot_id] = snapshot
            if len(self._by_id) > 500:
                oldest = next(iter(self._by_id))
                self._by_id.pop(oldest, None)
            return snapshot


analysis_snapshots = AnalysisSnapshotService()
