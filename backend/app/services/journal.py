"""Trade Journal and Weekly Review Service."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.trade import Trade


class JournalService:
    @staticmethod
    async def get_performance_summary(db: AsyncSession, days: int = 30) -> dict:
        """Compute performance analytics for closed trades in the given period."""
        since = datetime.now(timezone.utc) - timedelta(days=days)

        stmt = select(Trade).where(Trade.created_at >= since, Trade.status == "closed")
        res = await db.execute(stmt)
        trades = res.scalars().all()

        total = len(trades)
        if total == 0:
            return {
                "period_days": days,
                "total_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_rr": 0.0,
                "best_setup": None,
                "trades": [],
            }

        wins = [t for t in trades if (t.pnl or 0) > 0]
        losses = [t for t in trades if (t.pnl or 0) <= 0]
        win_rate = (len(wins) / total) * 100
        total_pnl = sum(t.pnl or 0 for t in trades)
        valid_rrs = [t.rr_ratio for t in trades if t.rr_ratio is not None]
        avg_rr = sum(valid_rrs) / len(valid_rrs) if valid_rrs else 0.0

        return {
            "period_days": days,
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_rr": round(avg_rr, 2),
        }
