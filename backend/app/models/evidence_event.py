"""Immutable Phase 3 decision-evidence event model."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, JSON, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base


JSON_DOCUMENT = JSON().with_variant(JSONB, "postgresql")


class EvidenceEvent(Base):
    """Append-only input/config/output snapshot for one deterministic decision."""

    __tablename__ = "evidence_events"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at = Column(DateTime(timezone=True), nullable=False, index=True)
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    event_type = Column(String(40), nullable=False, default="strategy_decision")
    source = Column(String(40), nullable=False)
    symbol = Column(String(30), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False, index=True)
    market_type = Column(String(20), nullable=False)
    exchange = Column(String(30), nullable=False)
    mode = Column(String(12), nullable=False, default="analysis")

    schema_version = Column(Integer, nullable=False)
    engine_version = Column(String(30), nullable=False)
    strategy_version = Column(String(30), nullable=False)
    indicator_version = Column(Integer, nullable=False)
    regime_version = Column(Integer, nullable=False)
    replayable = Column(Boolean, nullable=False, default=True)

    market_data_hash = Column(String(64), nullable=False)
    config_hash = Column(String(64), nullable=False)
    decision_hash = Column(String(64), nullable=False, index=True)
    payload_hash = Column(String(64), nullable=False)
    payload = Column(JSON_DOCUMENT, nullable=False)

    __table_args__ = (
        Index("ix_evidence_symbol_time", "symbol", "occurred_at"),
        Index("ix_evidence_source_time", "source", "occurred_at"),
        Index("ix_evidence_strategy_time", "strategy_version", "occurred_at"),
    )

