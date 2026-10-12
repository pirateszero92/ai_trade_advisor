"""
Bid/Ask Quote Model & Freshness Validator (DATA-003)
Enforces real-time quote validity before any execution decision.
"""

from datetime import datetime, timezone
import math
from pydantic import BaseModel, Field

from app.domain.reason_codes import ReasonCode
from app.market_data.quality import DataQuality, DataQualityMetadata


class MarketQuote(BaseModel):
    """Authoritative executable quote snapshot."""

    symbol: str
    bid: float
    ask: float
    last: float
    timestamp: datetime
    quality_meta: DataQualityMetadata = Field(default_factory=lambda: DataQualityMetadata())

    @property
    def spread_pct(self) -> float:
        if self.bid <= 0 or self.ask <= 0:
            return 0.0
        return (self.ask - self.bid) / self.bid

    def age_seconds(self, now: datetime | None = None) -> float:
        current = now or datetime.now(timezone.utc)
        ts = self.timestamp if self.timestamp.tzinfo else self.timestamp.replace(tzinfo=timezone.utc)
        return (current - ts).total_seconds()

    def validate_executable(
        self,
        *,
        max_age_seconds: float = 5.0,
        max_spread_pct: float = 0.01,
        max_future_skew_seconds: float = 1.0,
        now: datetime | None = None,
    ) -> tuple[bool, ReasonCode, str]:
        """
        Validates whether this quote satisfies execution invariants (Section 6.4 & 38).
        Returns (is_valid, reason_code, message).
        """
        if not (math.isfinite(self.bid) and math.isfinite(self.ask) and math.isfinite(self.last)):
            return False, ReasonCode.INVALID_BID_ASK, "Quote prices must be finite numbers"

        if self.bid <= 0 or self.ask <= 0 or self.last <= 0:
            return False, ReasonCode.INVALID_BID_ASK, f"Non-positive quote: bid={self.bid}, ask={self.ask}"

        if self.ask < self.bid:
            return False, ReasonCode.INVALID_BID_ASK, f"Inverted quote: ask ({self.ask}) < bid ({self.bid})"

        current = now or datetime.now(timezone.utc)
        ts = self.timestamp if self.timestamp.tzinfo else self.timestamp.replace(tzinfo=timezone.utc)
        skew = (ts - current).total_seconds()
        if skew > max_future_skew_seconds:
            return (
                False,
                ReasonCode.QUOTE_FUTURE_SKEW,
                f"Quote timestamp is in the future by {skew:.2f}s (tolerance: {max_future_skew_seconds}s)",
            )

        age = (current - ts).total_seconds()
        if age > max_age_seconds:
            return (
                False,
                ReasonCode.QUOTE_STALE,
                f"Quote is stale: age {age:.2f}s > max {max_age_seconds}s",
            )

        if self.spread_pct > max_spread_pct:
            return (
                False,
                ReasonCode.SPREAD_TOO_WIDE,
                f"Spread too wide: {self.spread_pct*100:.3f}% > max {max_spread_pct*100:.3f}%",
            )

        return True, ReasonCode.DATA_QUALITY_OK, "Quote satisfies execution invariants"
