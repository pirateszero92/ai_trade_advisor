"""Phase 3 ledger migration, backtest and release-gate models."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)

from app.models.base import Base
from app.models.evidence_event import JSON_DOCUMENT


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TradeLedgerRecord(Base):
    """PostgreSQL mirror of a Paper or Live trade ledger record."""

    __tablename__ = "trade_ledger_records"

    id = Column(String(64), primary_key=True)
    mode = Column(String(12), nullable=False, index=True)
    broker = Column(String(30), nullable=False)
    symbol = Column(String(30), nullable=False, index=True)
    direction = Column(String(10), nullable=False)
    status = Column(String(20), nullable=False, index=True)
    order_type = Column(String(20), nullable=False)
    entry_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    requested_quantity = Column(Float, nullable=True)
    filled_quantity = Column(Float, nullable=True)
    close_price = Column(Float, nullable=True)
    realized_pnl = Column(Float, nullable=True)
    mfe = Column(Float, nullable=True)
    mae = Column(Float, nullable=True)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    imported_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    mirrored_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    source_version = Column(Integer, nullable=False, default=1)
    source_payload = Column(JSON_DOCUMENT, nullable=False)

    __table_args__ = (
        Index("ix_trade_ledger_mode_status", "mode", "status"),
        Index("ix_trade_ledger_symbol_opened", "symbol", "opened_at"),
    )


class OrderLedgerRecord(Base):
    """Normalized entry/exit order derived from the compatibility ledger."""

    __tablename__ = "order_ledger_records"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trade_id = Column(
        String(64),
        ForeignKey("trade_ledger_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    leg = Column(String(12), nullable=False)
    client_order_id = Column(String(100), nullable=True, index=True)
    order_type = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)
    status = Column(String(24), nullable=False, index=True)
    limit_price = Column(Float, nullable=True)
    requested_quantity = Column(Float, nullable=False)
    filled_quantity = Column(Float, nullable=False, default=0.0)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    source_payload = Column(JSON_DOCUMENT, nullable=False)

    __table_args__ = (
        UniqueConstraint("trade_id", "leg", name="uq_order_ledger_trade_leg"),
    )


class FillLedgerRecord(Base):
    """Normalized immutable fill for entry, exit or partial execution."""

    __tablename__ = "fill_ledger_records"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("order_ledger_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trade_id = Column(
        String(64),
        ForeignKey("trade_ledger_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    leg = Column(String(12), nullable=False)
    filled_at = Column(DateTime(timezone=True), nullable=False, index=True)
    price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    fee = Column(Float, nullable=False, default=0.0)
    spread_cost = Column(Float, nullable=False, default=0.0)
    slippage_cost = Column(Float, nullable=False, default=0.0)
    latency_ms = Column(Integer, nullable=False, default=0)
    liquidity = Column(String(12), nullable=False, default="unknown")
    source_payload = Column(JSON_DOCUMENT, nullable=False)


class JsonMigrationCheckpoint(Base):
    __tablename__ = "json_migration_checkpoints"

    name = Column(String(80), primary_key=True)
    source_hash = Column(String(64), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    stats = Column(JSON_DOCUMENT, nullable=False)


class BacktestRun(Base):
    """Immutable deterministic batch replay or OOS backtest result."""

    __tablename__ = "backtest_runs"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    run_type = Column(String(24), nullable=False, index=True)
    status = Column(String(20), nullable=False)
    symbol = Column(String(30), nullable=True, index=True)
    timeframe = Column(String(10), nullable=True)
    strategy_version = Column(String(30), nullable=False, index=True)
    evaluation_mode = Column(String(30), nullable=False)
    data_start = Column(DateTime(timezone=True), nullable=True)
    data_end = Column(DateTime(timezone=True), nullable=True)
    config = Column(JSON_DOCUMENT, nullable=False)
    assumptions = Column(JSON_DOCUMENT, nullable=False)
    metrics = Column(JSON_DOCUMENT, nullable=False)
    result = Column(JSON_DOCUMENT, nullable=False)
    result_hash = Column(String(64), nullable=False)


class ReleaseGateEvaluation(Base):
    """Immutable deterministic gate result; never promotes a release itself."""

    __tablename__ = "release_gate_evaluations"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    backtest_run_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("backtest_runs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    passed = Column(Boolean, nullable=False)
    human_approval_required = Column(Boolean, nullable=False, default=True)
    production_eligible = Column(Boolean, nullable=False, default=False)
    criteria = Column(JSON_DOCUMENT, nullable=False)
    checks = Column(JSON_DOCUMENT, nullable=False)
    failure_reasons = Column(JSON_DOCUMENT, nullable=False)
    result_hash = Column(String(64), nullable=False)

