"""Short-lived, in-memory authorization for real-money operations.

Live sessions intentionally are not persisted.  A backend restart therefore
returns every client to paper mode and requires a fresh broker preflight and
explicit user confirmation before another live mutation can be attempted.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException

from app.core.security import verify_api_key


LIVE_SESSION_HEADER = "X-Live-Session-Token"


@dataclass(frozen=True)
class LiveSession:
    session_id: str
    broker: str
    created_at: datetime
    expires_at: datetime
    api_key_fingerprint: str


class LiveSessionManager:
    """Process-local store for opaque, short-lived live-trading tokens."""

    def __init__(self, now: Optional[Callable[[], datetime]] = None):
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._sessions: dict[str, LiveSession] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def fingerprint_api_key(api_key: str) -> str:
        if not api_key:
            return "anonymous"
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]

    def _purge_expired_locked(self) -> None:
        now = self._now()
        expired = [key for key, session in self._sessions.items() if session.expires_at <= now]
        for key in expired:
            self._sessions.pop(key, None)

    def issue(self, *, broker: str, api_key: str, ttl_minutes: int) -> tuple[str, LiveSession]:
        normalized_broker = broker.strip().lower()
        if normalized_broker not in {"innovestx", "binance", "bybit", "alpaca"}:
            raise ValueError(f"Live broker '{broker}' is not enabled")
        if ttl_minutes < 1 or ttl_minutes > 60:
            raise ValueError("Live session TTL must be between 1 and 60 minutes")

        token = secrets.token_urlsafe(48)
        now = self._now()
        session = LiveSession(
            session_id=secrets.token_hex(12),
            broker=normalized_broker,
            created_at=now,
            expires_at=now + timedelta(minutes=ttl_minutes),
            api_key_fingerprint=self.fingerprint_api_key(api_key),
        )
        with self._lock:
            self._purge_expired_locked()
            self._sessions[self._token_hash(token)] = session
        return token, session

    def get(
        self,
        token: str | None,
        *,
        broker: str | None = None,
        api_key: str | None = None,
    ) -> LiveSession | None:
        if not token:
            return None
        with self._lock:
            self._purge_expired_locked()
            session = self._sessions.get(self._token_hash(token))
            if session is None:
                return None
            if broker and session.broker != broker.strip().lower():
                return None
            if api_key and session.api_key_fingerprint != self.fingerprint_api_key(api_key):
                return None
            return session

    def require(
        self,
        token: str | None,
        *,
        broker: str | None = None,
        api_key: str | None = None,
    ) -> LiveSession:
        session = self.get(token, broker=broker, api_key=api_key)
        if session is None:
            raise HTTPException(
                status_code=401,
                detail="A valid, unexpired Live Session is required for this real-money operation",
            )
        return session

    def revoke(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            return self._sessions.pop(self._token_hash(token), None) is not None

    def revoke_all(self) -> int:
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            return count

    def active_count(self) -> int:
        with self._lock:
            self._purge_expired_locked()
            return len(self._sessions)


live_session_manager = LiveSessionManager()


async def require_live_session(
    x_live_session_token: str | None = Header(default=None, alias=LIVE_SESSION_HEADER),
    api_key: str = Depends(verify_api_key),
) -> LiveSession:
    return live_session_manager.require(x_live_session_token, api_key=api_key)
