"""Signal ORM Model."""

from datetime import datetime
import uuid
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base


class Signal(Base):
    __tablename__ = "signals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    symbol = Column(String(30), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False)
    market_type = Column(String(20), default="crypto")
    signal_type = Column(String(30), nullable=False)  # long_setup, short_setup, regime_change, risk_warning
    direction = Column(String(10), nullable=True)  # long, short, neutral, avoid

    confluence_score = Column(Integer, default=0)
    ai_message = Column(Text, nullable=False)
    ai_provider = Column(String(30), nullable=True)
    model_used = Column(String(50), nullable=True)

    levels = Column(JSONB, nullable=True)  # {entry, sl, tp, rr}
    smc_data = Column(JSONB, nullable=True)  # raw snapshot of SMC zones

    acknowledged = Column(Boolean, default=False)
    acted_upon = Column(Boolean, default=False)
