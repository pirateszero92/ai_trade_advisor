"""Authoritative PostgreSQL models for the Paper Order Management System.

These tables are intentionally separate from the Phase 3 analytical mirror.
They contain Paper state only and cannot represent a Live broker account.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)

from app.models.base import Base
from app.models.evidence_event import JSON_DOCUMENT


MONEY = Numeric(28, 10)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaperOMSAccount(Base):
    """One immutable Paper account generation.

    Resetting Paper trading retires the current generation and creates a new
    one. Historical fills are retained instead of being deleted.
    """

    __tablename__ = "paper_oms_accounts"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    active = Column(Boolean, nullable=False, default=True, index=True)
    initial_capital = Column(MONEY, nullable=False)
    currency = Column(String(8), nullable=False, default="USD")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    retired_at = Column(DateTime(timezone=True), nullable=True)


class PaperOMSPosition(Base):
    """Authoritative Paper position aggregate."""

    __tablename__ = "paper_oms_positions"

    id = Column(String(64), primary_key=True)
    account_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("paper_oms_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    symbol = Column(String(30), nullable=False, index=True)
    direction = Column(String(10), nullable=False)
    status = Column(String(20), nullable=False, index=True)
    order_type = Column(String(20), nullable=False)
    exchange = Column(String(30), nullable=False)
    tag = Column(String(100), nullable=False)
    notes = Column(String(4000), nullable=False, default="")
    requested_quantity = Column(MONEY, nullable=False)
    opened_quantity = Column(MONEY, nullable=False, default=0)
    closed_quantity = Column(MONEY, nullable=False, default=0)
    remaining_quantity = Column(MONEY, nullable=False, default=0)
    requested_entry_price = Column(MONEY, nullable=False)
    average_entry_price = Column(MONEY, nullable=True)
    average_exit_price = Column(MONEY, nullable=True)
    stop_loss = Column(MONEY, nullable=False)
    initial_stop_loss = Column(MONEY, nullable=False)
    take_profit = Column(MONEY, nullable=False)
    realized_pnl_gross = Column(MONEY, nullable=False, default=0)
    realized_pnl_net = Column(MONEY, nullable=False, default=0)
    fees_total = Column(MONEY, nullable=False, default=0)
    spread_cost_total = Column(MONEY, nullable=False, default=0)
    slippage_cost_total = Column(MONEY, nullable=False, default=0)
    risk_pct = Column(MONEY, nullable=False)
    auto_be = Column(Boolean, nullable=False, default=True)
    trailing_stop = Column(Boolean, nullable=False, default=False)
    favorable_extreme = Column(MONEY, nullable=True)
    max_r_multiple = Column(MONEY, nullable=False, default=0)
    protection_stage = Column(String(32), nullable=False, default="initial")
    protection_updated_at = Column(DateTime(timezone=True), nullable=True)
    close_reason = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    version = Column(Integer, nullable=False, default=1)
    source_payload = Column(JSON_DOCUMENT, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_paper_oms_position_account_status", "account_id", "status"),
        Index("ix_paper_oms_position_symbol_status", "symbol", "status"),
    )


class PaperOMSOrder(Base):
    """Paper entry or reduce order with an explicit position effect."""

    __tablename__ = "paper_oms_orders"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("paper_oms_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    position_id = Column(
        String(64),
        ForeignKey("paper_oms_positions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    client_order_id = Column(String(100), nullable=False)
    leg = Column(String(12), nullable=False)
    position_effect = Column(String(12), nullable=False)
    side = Column(String(10), nullable=False)
    order_type = Column(String(20), nullable=False)
    status = Column(String(24), nullable=False, index=True)
    requested_quantity = Column(MONEY, nullable=False)
    filled_quantity = Column(MONEY, nullable=False, default=0)
    remaining_quantity = Column(MONEY, nullable=False)
    limit_price = Column(MONEY, nullable=True)
    average_fill_price = Column(MONEY, nullable=True)
    close_reason = Column(String(200), nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    version = Column(Integer, nullable=False, default=1)
    source_payload = Column(JSON_DOCUMENT, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("account_id", "client_order_id", name="uq_paper_oms_account_client_order"),
        Index("ix_paper_oms_order_position_status", "position_id", "status"),
    )


class PaperOMSFill(Base):
    """Immutable Paper execution fill with modeled transaction costs."""

    __tablename__ = "paper_oms_fills"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("paper_oms_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    order_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("paper_oms_orders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    position_id = Column(
        String(64),
        ForeignKey("paper_oms_positions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    execution_key = Column(String(180), nullable=False, unique=True)
    leg = Column(String(12), nullable=False)
    position_effect = Column(String(12), nullable=False)
    side = Column(String(10), nullable=False)
    reference_price = Column(MONEY, nullable=False)
    fill_price = Column(MONEY, nullable=False)
    quantity = Column(MONEY, nullable=False)
    fee = Column(MONEY, nullable=False, default=0)
    spread_cost = Column(MONEY, nullable=False, default=0)
    slippage_cost = Column(MONEY, nullable=False, default=0)
    liquidity = Column(String(16), nullable=False)
    source = Column(String(40), nullable=False)
    source_sequence = Column(String(80), nullable=True)
    exchange_timestamp = Column(DateTime(timezone=True), nullable=True)
    received_timestamp = Column(DateTime(timezone=True), nullable=True)
    filled_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    latency_ms = Column(Integer, nullable=False, default=0)
    source_payload = Column(JSON_DOCUMENT, nullable=False, default=dict)


class PaperOMSEvent(Base):
    """Append-only state-transition audit record."""

    __tablename__ = "paper_oms_events"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("paper_oms_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    position_id = Column(
        String(64),
        ForeignKey("paper_oms_positions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    order_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("paper_oms_orders.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    event_key = Column(String(180), nullable=False, unique=True)
    event_type = Column(String(40), nullable=False, index=True)
    previous_status = Column(String(24), nullable=True)
    new_status = Column(String(24), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    payload = Column(JSON_DOCUMENT, nullable=False, default=dict)
