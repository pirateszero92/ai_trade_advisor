"""
Market Data Quality Contracts (DATA-005)
Ensures every market observation carries explicit source and quality metadata.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class DataQuality(str, Enum):
    EXCHANGE_NATIVE = "EXCHANGE_NATIVE"  # Direct exchange websocket / REST
    BROKER_NATIVE = "BROKER_NATIVE"      # Direct broker API (Alpaca, MT5, InnovestX)
    DERIVED = "DERIVED"                  # Calculated deterministically from native data
    INFERRED = "INFERRED"                # Estimated (e.g. tick rule delta from trades)
    SYNTHETIC = "SYNTHETIC"              # Generated / simulated / proxy
    STALE = "STALE"                      # Data exceeded max allowable age
    MISSING = "MISSING"                  # Field not provided by source
    UNKNOWN = "UNKNOWN"                  # Unclassified source


class DataQualityMetadata(BaseModel):
    """Quality descriptor attached to quotes, candles, and facts."""

    quality: DataQuality = DataQuality.UNKNOWN
    source: str = "unknown"
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    exchange_timestamp: datetime | None = None
    age_ms: float = 0.0

    @classmethod
    def create(
        cls,
        quality: DataQuality,
        source: str,
        exchange_timestamp: datetime | None = None,
        now: datetime | None = None,
    ) -> "DataQualityMetadata":
        current = now or datetime.now(timezone.utc)
        age = 0.0
        if exchange_timestamp:
            ts = exchange_timestamp if exchange_timestamp.tzinfo else exchange_timestamp.replace(tzinfo=timezone.utc)
            age = max(0.0, (current - ts).total_seconds() * 1000.0)
        return cls(
            quality=quality,
            source=source,
            received_at=current,
            exchange_timestamp=exchange_timestamp,
            age_ms=age,
        )
