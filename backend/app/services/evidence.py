"""Phase 3 immutable decision evidence and deterministic replay helpers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any
import uuid

from loguru import logger
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.strategy_config_store import read_strategy_config
from app.models.base import async_session_factory
from app.models.evidence_event import EvidenceEvent


EVIDENCE_SCHEMA_VERSION = 1
DECISION_ENGINE_VERSION = "3.1.0"
MAX_REPLAY_BARS = 2000
STRATEGY_FILE = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"


def current_decision_config(strategy_engine: Any) -> dict[str, Any]:
    """Capture the exact strategy plus dynamic indicator/regime configuration."""
    from app.engines.indicator_core import load_indicator_core_config
    from app.engines.regime_engine import load_regime_policy_config
    from app.engines.timeframe_profiles import load_timeframe_profiles

    config = strategy_engine.config_snapshot
    config["indicator_core"] = load_indicator_core_config()
    config["regime_policy"] = load_regime_policy_config()
    config["timeframe_profiles"] = load_timeframe_profiles()
    return config


@dataclass(frozen=True)
class EvidenceEnvelope:
    event_id: uuid.UUID
    occurred_at: datetime
    event_type: str
    source: str
    symbol: str
    timeframe: str
    market_type: str
    exchange: str
    mode: str
    schema_version: int
    engine_version: str
    strategy_version: str
    indicator_version: int
    regime_version: int
    market_data_hash: str
    config_hash: str
    decision_hash: str
    payload_hash: str
    replayable: bool
    payload: dict[str, Any]


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, pd.Timestamp)):
        timestamp = value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Unsupported evidence value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize evidence deterministically so fingerprints are reproducible."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _finite_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def serialize_market_window(df: pd.DataFrame) -> dict[str, Any]:
    """Return a bounded, canonical OHLCV snapshot suitable for offline replay."""
    if df is None or df.empty:
        raise ValueError("Cannot record an empty market-data window")
    if len(df) > MAX_REPLAY_BARS:
        raise ValueError(f"Evidence window exceeds {MAX_REPLAY_BARS} bars")

    normalized = df.copy()
    normalized.columns = [str(column).lower() for column in normalized.columns]
    required = {"open", "high", "low", "close"}
    missing = required - set(normalized.columns)
    if missing:
        raise ValueError(f"Market-data window is missing columns: {sorted(missing)}")

    candles: list[dict[str, Any]] = []
    for index, row in normalized.iterrows():
        timestamp = pd.Timestamp(index)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        candle = {
            "t": timestamp.isoformat(),
            "o": _finite_or_none(row.get("open")),
            "h": _finite_or_none(row.get("high")),
            "l": _finite_or_none(row.get("low")),
            "c": _finite_or_none(row.get("close")),
            "v": _finite_or_none(row.get("volume")),
        }
        optional_flow_fields = {
            "bv": "buy_volume",
            "sv": "sell_volume",
            "vd": "volume_delta",
            "cvd": "cvd",
        }
        for compact_key, column in optional_flow_fields.items():
            value = _finite_or_none(row.get(column))
            if value is not None:
                candle[compact_key] = value
        flow_source = row.get("flow_source")
        if isinstance(flow_source, str) and flow_source:
            candle["flow_source"] = flow_source
        if any(candle[key] is None for key in ("o", "h", "l", "c")):
            raise ValueError("Market-data window contains non-finite OHLC values")
        candles.append(candle)

    return {
        "format": "ohlcv.v1",
        "bar_count": len(candles),
        "first_timestamp": candles[0]["t"],
        "last_timestamp": candles[-1]["t"],
        "candles": candles,
    }


def deserialize_market_window(snapshot: dict[str, Any]) -> pd.DataFrame:
    if not isinstance(snapshot, dict) or snapshot.get("format") != "ohlcv.v1":
        raise ValueError("Unsupported or missing market-data snapshot format")
    candles = snapshot.get("candles")
    if not isinstance(candles, list) or not candles:
        raise ValueError("Evidence contains no candles")
    if len(candles) > MAX_REPLAY_BARS:
        raise ValueError(f"Evidence contains more than {MAX_REPLAY_BARS} bars")

    rows: list[dict[str, float | None]] = []
    timestamps: list[pd.Timestamp] = []
    for candle in candles:
        if not isinstance(candle, dict):
            raise ValueError("Invalid candle in evidence payload")
        timestamp = pd.Timestamp(candle.get("t"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        row = {
            "open": _finite_or_none(candle.get("o")),
            "high": _finite_or_none(candle.get("h")),
            "low": _finite_or_none(candle.get("l")),
            "close": _finite_or_none(candle.get("c")),
            "volume": _finite_or_none(candle.get("v")),
        }
        optional_flow_fields = {
            "buy_volume": "bv",
            "sell_volume": "sv",
            "volume_delta": "vd",
            "cvd": "cvd",
        }
        for column, compact_key in optional_flow_fields.items():
            value = _finite_or_none(candle.get(compact_key))
            if value is not None:
                row[column] = value
        flow_source = candle.get("flow_source")
        if isinstance(flow_source, str) and flow_source:
            row["flow_source"] = flow_source
        if any(row[key] is None for key in ("open", "high", "low", "close")):
            raise ValueError("Replay candle contains non-finite OHLC values")
        timestamps.append(timestamp)
        rows.append(row)

    frame = pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps))
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("Replay candle timestamps must be unique and increasing")
    return frame


def build_decision_evidence(
    *,
    source: str,
    symbol: str,
    timeframe: str,
    market_type: str,
    exchange: str,
    market_data: pd.DataFrame,
    htf_bias: str,
    entry_mode: str,
    signal: dict[str, Any],
    strategy: dict[str, Any],
    risk: dict[str, Any] | None = None,
    ai_analysis: dict[str, Any] | None = None,
    mode: str = "analysis",
    occurred_at: datetime | None = None,
    config_snapshot: dict[str, Any] | None = None,
    mtf_market_data: dict[str, pd.DataFrame] | None = None,
    mtf_decision: dict[str, Any] | None = None,
) -> EvidenceEnvelope:
    """Build, validate and fingerprint one immutable decision event."""
    event_id = uuid.uuid4()
    event_time = occurred_at or datetime.now(timezone.utc)
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    event_time = event_time.astimezone(timezone.utc)

    market_snapshot = serialize_market_window(market_data)
    config = deepcopy(config_snapshot or read_strategy_config(STRATEGY_FILE))
    indicator_config = config.get("indicator_core") or {}
    regime_config = config.get("regime_policy") or {}
    strategy_version = str(config.get("version", "unknown"))[:30]
    indicator_version = int(indicator_config.get("version", 0))
    regime_version = int(regime_config.get("version", 0))
    deterministic_output = {
        "signal": deepcopy(signal),
        "strategy": deepcopy(strategy),
    }
    mtf_snapshot: dict[str, Any] | None = None
    if (mtf_market_data is None) != (mtf_decision is None):
        raise ValueError("MTF evidence requires both market windows and decision")
    if mtf_market_data is not None:
        required_roles = {"bias", "setup", "trigger"}
        if set(mtf_market_data) != required_roles:
            raise ValueError("MTF evidence must contain bias, setup and trigger windows")
        mtf_snapshot = {
            role: serialize_market_window(mtf_market_data[role])
            for role in ("bias", "setup", "trigger")
        }
        deterministic_output["mtf"] = deepcopy(mtf_decision)

    market_hash_target: Any = market_snapshot
    if mtf_snapshot is not None:
        market_hash_target = {"primary": market_snapshot, "mtf": mtf_snapshot}
    market_hash = fingerprint(market_hash_target)
    config_digest = fingerprint(config)
    decision_digest = fingerprint(deterministic_output)
    payload = {
        "event_id": str(event_id),
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "engine_version": DECISION_ENGINE_VERSION,
        "event_type": "strategy_decision",
        "source": source,
        "occurred_at": event_time.isoformat(),
        "context": {
            "symbol": symbol,
            "timeframe": timeframe,
            "market_type": market_type,
            "exchange": exchange,
            "mode": mode,
            "htf_bias": htf_bias,
            "entry_mode": entry_mode,
        },
        "market_data": market_snapshot,
        "config": config,
        "decision": {
            **deterministic_output,
            "risk": deepcopy(risk),
            "ai_analysis": deepcopy(ai_analysis),
        },
        "integrity": {
            "market_data_hash": market_hash,
            "config_hash": config_digest,
            "decision_hash": decision_digest,
        },
    }
    if mtf_snapshot is not None:
        payload["mtf_market_data"] = mtf_snapshot
    payload_digest = fingerprint(payload)
    return EvidenceEnvelope(
        event_id=event_id,
        occurred_at=event_time,
        event_type="strategy_decision",
        source=source,
        symbol=symbol,
        timeframe=timeframe,
        market_type=market_type,
        exchange=exchange,
        mode=mode,
        schema_version=EVIDENCE_SCHEMA_VERSION,
        engine_version=DECISION_ENGINE_VERSION,
        strategy_version=strategy_version,
        indicator_version=indicator_version,
        regime_version=regime_version,
        market_data_hash=market_hash,
        config_hash=config_digest,
        decision_hash=decision_digest,
        payload_hash=payload_digest,
        replayable=bool(indicator_config and regime_config),
        payload=payload,
    )


def evidence_model(envelope: EvidenceEnvelope) -> EvidenceEvent:
    return EvidenceEvent(
        id=envelope.event_id,
        occurred_at=envelope.occurred_at,
        event_type=envelope.event_type,
        source=envelope.source,
        symbol=envelope.symbol,
        timeframe=envelope.timeframe,
        market_type=envelope.market_type,
        exchange=envelope.exchange,
        mode=envelope.mode,
        schema_version=envelope.schema_version,
        engine_version=envelope.engine_version,
        strategy_version=envelope.strategy_version,
        indicator_version=envelope.indicator_version,
        regime_version=envelope.regime_version,
        market_data_hash=envelope.market_data_hash,
        config_hash=envelope.config_hash,
        decision_hash=envelope.decision_hash,
        payload_hash=envelope.payload_hash,
        replayable=envelope.replayable,
        payload=envelope.payload,
    )


async def append_evidence_event(
    envelope: EvidenceEnvelope,
    session: AsyncSession,
) -> EvidenceEvent:
    """Insert only; this service intentionally exposes no update/delete operation."""
    event = evidence_model(envelope)
    session.add(event)
    await session.flush()
    return event


async def capture_decision_evidence(**kwargs: Any) -> dict[str, Any]:
    """Persist evidence without allowing telemetry failure to change a decision."""
    try:
        envelope = build_decision_evidence(**kwargs)
        async with async_session_factory() as session:
            try:
                await append_evidence_event(envelope, session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        return {
            "status": "persisted",
            "event_id": str(envelope.event_id),
            "decision_hash": envelope.decision_hash,
            "schema_version": envelope.schema_version,
        }
    except Exception as exc:
        logger.error("[Evidence] Could not persist decision evidence: {}", exc)
        return {
            "status": "unavailable",
            "event_id": None,
            "error_code": "EVIDENCE_STORE_UNAVAILABLE",
        }


def verify_evidence_integrity(event: EvidenceEvent) -> list[str]:
    failures: list[str] = []
    payload = event.payload
    if fingerprint(payload) != event.payload_hash:
        failures.append("payload_hash_mismatch")
        return failures
    integrity = payload.get("integrity", {}) if isinstance(payload, dict) else {}
    market_hash_target: Any = payload.get("market_data")
    if payload.get("mtf_market_data") is not None:
        market_hash_target = {
            "primary": payload.get("market_data"),
            "mtf": payload.get("mtf_market_data"),
        }
    if fingerprint(market_hash_target) != integrity.get("market_data_hash"):
        failures.append("market_data_hash_mismatch")
    if fingerprint(payload.get("config")) != integrity.get("config_hash"):
        failures.append("config_hash_mismatch")
    deterministic_output = {
        "signal": payload.get("decision", {}).get("signal"),
        "strategy": payload.get("decision", {}).get("strategy"),
    }
    if payload.get("decision", {}).get("mtf") is not None:
        deterministic_output["mtf"] = payload.get("decision", {}).get("mtf")
    if fingerprint(deterministic_output) != integrity.get("decision_hash"):
        failures.append("decision_hash_mismatch")
    return failures


def replay_decision_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Replay SMC and Strategy from a stored event without external market I/O."""
    if payload.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise ValueError("Unsupported evidence schema version")
    integrity = payload.get("integrity") or {}
    market_hash_target: Any = payload.get("market_data")
    if payload.get("mtf_market_data") is not None:
        market_hash_target = {
            "primary": payload.get("market_data"),
            "mtf": payload.get("mtf_market_data"),
        }
    if fingerprint(market_hash_target) != integrity.get("market_data_hash"):
        raise ValueError("Market-data evidence hash mismatch")
    if fingerprint(payload.get("config")) != integrity.get("config_hash"):
        raise ValueError("Configuration evidence hash mismatch")

    from app.engines.smc_engine import SMCEngine
    from app.engines.strategy_engine import StrategyEngine

    context = payload.get("context") or {}
    config = payload.get("config") or {}
    mtf_market_data = payload.get("mtf_market_data")
    if mtf_market_data is not None:
        from app.services.mtf_analysis import (
            analyze_mtf_frames,
            mtf_snapshot_id,
        )

        frames = {
            role: deserialize_market_window(mtf_market_data.get(role) or {})
            for role in ("bias", "setup", "trigger")
        }
        snapshot_id = mtf_snapshot_id(
            frames=frames,
            symbol=str(context.get("symbol", "")),
            market_type=str(context.get("market_type", "")),
            exchange=str(context.get("exchange", "")),
            entry_mode=str(context.get("entry_mode", "limit")),
            config_snapshot=config,
        )
        mtf = analyze_mtf_frames(
            frames=frames,
            symbol=str(context.get("symbol", "")),
            entry_mode=str(context.get("entry_mode", "limit")),
            config_snapshot=config,
            snapshot_id=snapshot_id,
        )
        replayed = {
            "signal": mtf.trigger_signal.to_dict(),
            "strategy": mtf.strategy.to_dict(),
            "mtf": mtf.decision_dict(),
        }
    else:
        frame = deserialize_market_window(payload.get("market_data") or {})
        signal = SMCEngine().analyze(
            frame,
            symbol=str(context.get("symbol", "")),
            timeframe=str(context.get("timeframe", "")),
            htf_bias=str(context.get("htf_bias", "neutral")),
            entry_mode=str(context.get("entry_mode", "limit")),
            indicator_config=config.get("indicator_core"),
            regime_config=config.get("regime_policy"),
        )
        strategy = StrategyEngine(strategy_config=config).evaluate(signal)
        if strategy.effective_policy:
            signal.market_regime["effective_policy"] = strategy.effective_policy
        replayed = {
            "signal": signal.to_dict(),
            "strategy": strategy.to_dict(),
        }
    replay_hash = fingerprint(replayed)
    expected_hash = str(integrity.get("decision_hash", ""))
    return {
        "match": replay_hash == expected_hash,
        "expected_decision_hash": expected_hash,
        "replayed_decision_hash": replay_hash,
        "engine_version_recorded": payload.get("engine_version"),
        "engine_version_replayed": DECISION_ENGINE_VERSION,
        "recorded": {
            "signal": payload.get("decision", {}).get("signal"),
            "strategy": payload.get("decision", {}).get("strategy"),
            **(
                {"mtf": payload.get("decision", {}).get("mtf")}
                if payload.get("decision", {}).get("mtf") is not None else {}
            ),
        },
        "replayed": replayed,
    }
