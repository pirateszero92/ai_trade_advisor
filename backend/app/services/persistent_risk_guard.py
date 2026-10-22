"""Restart-safe portfolio circuit breaker backed by Paper OMS history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models.base import async_session_factory
from app.models.paper_oms import PaperOMSAccount, PaperOMSPosition, PaperOMSRiskHalt


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized is not None else None


class PersistentRiskGuard:
    """Own durable portfolio halts without making OMS a risk authority."""

    HALT_DURATION = timedelta(hours=2)
    CONSECUTIVE_STOP_LIMIT = 4

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_factory = session_factory or async_session_factory

    @staticmethod
    async def _active_account(
        session: AsyncSession,
        *,
        lock: bool = False,
    ) -> PaperOMSAccount:
        stmt = (
            select(PaperOMSAccount)
            .where(PaperOMSAccount.active.is_(True))
            .order_by(PaperOMSAccount.created_at.desc())
            .limit(1)
        )
        if lock:
            stmt = stmt.with_for_update()
        account = (await session.execute(stmt)).scalar_one_or_none()
        if account is None:
            raise RuntimeError("Paper OMS has no active account generation")
        return account

    async def active_halt(self, *, now: datetime | None = None) -> dict[str, Any] | None:
        from app.core.runtime_config import load_runtime_config

        now = now or _utcnow()
        cfg = get_settings()
        r_cfg = load_runtime_config()
        streak_enabled = bool(
            r_cfg.get("loss_streak_halt_enabled", getattr(cfg, "loss_streak_halt_enabled", True))
        )
        streak_hours = int(
            r_cfg.get("loss_streak_halt_hours", getattr(cfg, "loss_streak_halt_hours", 2)) or 2
        )
        streak_duration = timedelta(hours=max(1, min(24, streak_hours)))

        async with self._session_factory() as session:
            account = await self._active_account(session)
            halt = (
                await session.execute(
                    select(PaperOMSRiskHalt)
                    .where(
                        PaperOMSRiskHalt.account_id == account.id,
                        PaperOMSRiskHalt.halted_until > now,
                    )
                    .order_by(PaperOMSRiskHalt.triggered_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not halt:
                return None
            if halt.reason == "three_consecutive_stop_losses":
                if not streak_enabled:
                    return None
                trig = _as_utc(halt.triggered_at)
                if trig is not None and (trig + streak_duration) <= now:
                    return None
            return self._serialize(halt)

    async def evaluate_and_persist(
        self,
        *,
        now: datetime | None = None,
        rolling_loss_limit_pct: float | None = None,
    ) -> dict[str, Any] | None:
        """Return active/new halt when rolling loss or stop streak requires one."""
        from app.core.runtime_config import load_runtime_config

        now = now or _utcnow()
        cfg = get_settings()
        r_cfg = load_runtime_config()
        streak_enabled = bool(
            r_cfg.get("loss_streak_halt_enabled", getattr(cfg, "loss_streak_halt_enabled", True))
        )
        streak_hours = int(
            r_cfg.get("loss_streak_halt_hours", getattr(cfg, "loss_streak_halt_hours", 2)) or 2
        )
        streak_duration = timedelta(hours=max(1, min(24, streak_hours)))

        limit_pct = (
            float(rolling_loss_limit_pct)
            if rolling_loss_limit_pct is not None
            else float(cfg.max_daily_loss)
        )
        if not 0 < limit_pct <= 100:
            raise ValueError("rolling_loss_limit_pct must be between 0 and 100")

        async with self._session_factory() as session:
            try:
                account = await self._active_account(session, lock=True)
                latest_halt = (
                    await session.execute(
                        select(PaperOMSRiskHalt)
                        .where(PaperOMSRiskHalt.account_id == account.id)
                        .order_by(PaperOMSRiskHalt.triggered_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()

                if latest_halt:
                    effective_until = _as_utc(latest_halt.halted_until) or now
                    if latest_halt.reason == "three_consecutive_stop_losses":
                        if not streak_enabled:
                            effective_until = now
                        elif latest_halt.triggered_at is not None:
                            effective_until = min(
                                effective_until,
                                (_as_utc(latest_halt.triggered_at) or now) + streak_duration,
                            )
                    if effective_until > now:
                        return self._serialize(latest_halt)

                cutoff = now - timedelta(hours=24)
                closed = (
                    await session.execute(
                        select(PaperOMSPosition)
                        .where(
                            PaperOMSPosition.account_id == account.id,
                            PaperOMSPosition.status == "closed",
                            PaperOMSPosition.closed_at.is_not(None),
                            PaperOMSPosition.closed_at >= cutoff,
                        )
                        .order_by(PaperOMSPosition.closed_at.desc())
                    )
                ).scalars().all()

                if not closed:
                    return None

                newest_close = _as_utc(closed[0].closed_at)
                latest_triggered_at = (
                    _as_utc(latest_halt.triggered_at)
                    if latest_halt is not None
                    else None
                )
                if (
                    latest_triggered_at is not None
                    and newest_close is not None
                    and newest_close <= latest_triggered_at
                ):
                    return None

                rolling_pnl = sum(float(item.realized_pnl_net or 0.0) for item in closed)
                initial_capital = float(account.initial_capital or 0.0)
                rolling_loss_pct = (
                    rolling_pnl / initial_capital * 100.0
                    if initial_capital > 0
                    else 0.0
                )

                recent_three = closed[: self.CONSECUTIVE_STOP_LIMIT]
                stop_streak = (
                    streak_enabled
                    and len(recent_three) == self.CONSECUTIVE_STOP_LIMIT
                    and all(
                        str(item.close_reason or "").lower().startswith("stop loss")
                        for item in recent_three
                    )
                )

                trigger = None
                if stop_streak:
                    trigger = "three_consecutive_stop_losses"
                elif rolling_loss_pct <= -limit_pct:
                    trigger = "rolling_24h_loss_limit"

                if trigger is None:
                    return None

                applied_duration = (
                    streak_duration
                    if trigger == "three_consecutive_stop_losses"
                    else self.HALT_DURATION
                )
                halt = PaperOMSRiskHalt(
                    account_id=account.id,
                    reason=trigger,
                    triggered_at=now,
                    halted_until=now + applied_duration,
                    source_payload={
                        "trigger": trigger,
                        "rolling_window_hours": 24,
                        "rolling_realized_pnl": round(rolling_pnl, 8),
                        "rolling_realized_pnl_pct": round(rolling_loss_pct, 6),
                        "rolling_loss_limit_pct": limit_pct,
                        "halt_hours": int(applied_duration.total_seconds() // 3600),
                        "consecutive_stop_losses": (
                            self.CONSECUTIVE_STOP_LIMIT if stop_streak else 0
                        ),
                        "latest_closed_at": _iso(newest_close),
                        "closed_trade_count_24h": len(closed),
                    },
                )
                session.add(halt)
                await session.commit()
                return self._serialize(halt)
            except Exception:
                await session.rollback()
                raise

    async def cluster_loss_cooldown(
        self,
        *,
        symbol: str,
        direction: str,
        now: datetime | None = None,
        cooldown_minutes: int | None = None,
    ) -> dict[str, Any] | None:
        """Return a restart-safe directional cluster cooldown after an SL.

        This is derived from the durable OMS ledger, so no in-memory timer can
        be lost on restart.  Opposite-direction opportunities remain eligible.
        """
        from app.engines.risk_engine import get_asset_cluster
        from app.core.runtime_config import load_runtime_config

        cfg = get_settings()
        r_cfg = load_runtime_config()
        enabled = bool(r_cfg.get("cluster_loss_cooldown_enabled", cfg.cluster_loss_cooldown_enabled))
        if not enabled:
            return None
        minutes = int(cooldown_minutes or r_cfg.get("cluster_loss_cooldown_minutes", cfg.cluster_loss_cooldown_minutes))
        if not 5 <= minutes <= 240:
            raise ValueError("cluster cooldown must be between 5 and 240 minutes")
        cluster = get_asset_cluster(symbol)
        if cluster == "other":
            return None
        normalized_direction = str(direction or "").lower()
        if normalized_direction not in {"long", "short"}:
            return None
        current = now or _utcnow()
        cutoff = current - timedelta(minutes=minutes)
        async with self._session_factory() as session:
            account = await self._active_account(session)
            recent = (
                await session.execute(
                    select(PaperOMSPosition)
                    .where(
                        PaperOMSPosition.account_id == account.id,
                        PaperOMSPosition.status == "closed",
                        PaperOMSPosition.direction == normalized_direction,
                        PaperOMSPosition.closed_at.is_not(None),
                        PaperOMSPosition.closed_at >= cutoff,
                    )
                    .order_by(PaperOMSPosition.closed_at.desc())
                )
            ).scalars().all()
        for position in recent:
            if get_asset_cluster(position.symbol) != cluster:
                continue
            reason = str(position.close_reason or "").lower()
            if not reason.startswith("stop loss"):
                continue
            closed_at = _as_utc(position.closed_at) or current
            blocked_until = closed_at + timedelta(minutes=minutes)
            if blocked_until <= current:
                continue
            return {
                "reason_code": "CLUSTER_LOSS_COOLDOWN",
                "cluster": cluster,
                "direction": normalized_direction,
                "trigger_symbol": position.symbol,
                "trigger_position_id": str(position.id),
                "triggered_at": closed_at.isoformat(),
                "blocked_until": blocked_until.isoformat(),
                "cooldown_minutes": minutes,
            }
        return None

    @staticmethod
    def _serialize(halt: PaperOMSRiskHalt) -> dict[str, Any]:
        return {
            "id": str(halt.id),
            "reason": halt.reason,
            "triggered_at": _iso(halt.triggered_at),
            "halted_until": _iso(halt.halted_until),
            "source_payload": dict(halt.source_payload or {}),
        }


persistent_risk_guard = PersistentRiskGuard()
