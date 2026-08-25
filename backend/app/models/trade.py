"""Trade ORM Model."""

from datetime import datetime
import uuid
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base


class Trade(Base):
    __tablename__ = "trades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    symbol = Column(String(30), nullable=False, index=True)
    exchange = Column(String(30), nullable=False, default="binance")
    market_type = Column(String(20), nullable=False, default="crypto")  # crypto, forex, stock
    timeframe = Column(String(10), nullable=False, default="1h")
    mode = Column(String(10), nullable=False, default="paper")  # paper, live
    direction = Column(String(10), nullable=False)  # long, short

    # SMC Context at entry
    htf_bias = Column(String(10), nullable=True)
    swing_bos = Column(Boolean, default=False)
    swing_choch = Column(Boolean, default=False)
    order_block_zone = Column(JSONB, nullable=True)
    fvg_zone = Column(JSONB, nullable=True)
    liquidity_sweep = Column(Boolean, default=False)
    in_discount = Column(Boolean, default=False)
    in_premium = Column(Boolean, default=False)

    # Execution levels
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)
    rr_ratio = Column(Float, nullable=True)
    risk_pct = Column(Float, default=1.0)
    position_size = Column(Float, nullable=False)

    # AI metadata
    strategy_version = Column(String(30), default="v1")
    prompt_version = Column(String(30), default="advisor_v1")
    ai_provider = Column(String(30), nullable=True)
    ai_score = Column(Integer, nullable=True)
    ai_reasoning = Column(Text, nullable=True)

    # Trade status & performance
    status = Column(String(20), default="open", index=True)  # open, closed, cancelled
    close_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)  # Absolute PnL
    pnl_pct = Column(Float, nullable=True)  # Percentage PnL
    mfe = Column(Float, nullable=True)  # Max Favorable Excursion
    mae = Column(Float, nullable=True)  # Max Adverse Excursion
    exit_reason = Column(String(50), nullable=True)  # tp_hit, sl_hit, manual, trailing
