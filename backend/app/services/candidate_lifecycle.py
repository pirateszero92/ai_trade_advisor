"""Deterministic lifecycle state machine for one Tri-Core trade candidate."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from app.services.observability import observability
from loguru import logger


class CandidateState(StrEnum):
    DETECTED = "DETECTED"
    TRI_CORE_READY = "TRI_CORE_READY"
    AI_REVIEW_PENDING = "AI_REVIEW_PENDING"
    AI_COHERENT = "AI_COHERENT"
    AI_CAUTION = "AI_CAUTION"
    AI_CONFLICT = "AI_CONFLICT"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    EXECUTION_VALIDATING = "EXECUTION_VALIDATING"
    EXECUTION_VALIDATED = "EXECUTION_VALIDATED"
    WAITING_ENTRY = "WAITING_ENTRY"
    LIMIT_ARMED = "LIMIT_ARMED"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REJECTED = "RISK_REJECTED"
    ORDER_PENDING = "ORDER_PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CLOSED = "CLOSED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


TERMINAL_STATES = {
    CandidateState.AI_CONFLICT,
    CandidateState.EXECUTION_REJECTED,
    CandidateState.RISK_REJECTED,
    CandidateState.CLOSED,
    CandidateState.INVALIDATED,
    CandidateState.EXPIRED,
}

ACTIVE_EXECUTION_STATES = {
    CandidateState.ORDER_PENDING,
    CandidateState.LIMIT_ARMED,
    CandidateState.OPEN,
    CandidateState.PARTIALLY_FILLED,
}

ALLOWED_TRANSITIONS: dict[CandidateState, set[CandidateState]] = {
    CandidateState.DETECTED: {
        CandidateState.TRI_CORE_READY,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.TRI_CORE_READY: {
        CandidateState.AI_REVIEW_PENDING,
        CandidateState.AI_COHERENT,  # already-reviewed synchronous path
        CandidateState.AI_CAUTION,
        CandidateState.AI_CONFLICT,
        CandidateState.AI_UNAVAILABLE,
        CandidateState.EXECUTION_VALIDATING,  # deterministic-only mode
        CandidateState.WAITING_ENTRY,
        CandidateState.LIMIT_ARMED,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.AI_REVIEW_PENDING: {
        CandidateState.AI_COHERENT,
        CandidateState.AI_CAUTION,
        CandidateState.AI_CONFLICT,
        CandidateState.AI_UNAVAILABLE,
        CandidateState.EXECUTION_VALIDATING,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.AI_COHERENT: {
        CandidateState.EXECUTION_VALIDATING,
        CandidateState.WAITING_ENTRY,
        CandidateState.LIMIT_ARMED,
        CandidateState.RISK_REJECTED,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.AI_CAUTION: {
        CandidateState.EXECUTION_VALIDATING,
        CandidateState.WAITING_ENTRY,
        CandidateState.LIMIT_ARMED,
        CandidateState.RISK_REJECTED,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.AI_UNAVAILABLE: {
        CandidateState.AI_REVIEW_PENDING,
        CandidateState.AI_COHERENT,
        CandidateState.AI_CAUTION,
        CandidateState.AI_CONFLICT,
        CandidateState.EXECUTION_VALIDATING,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.EXECUTION_VALIDATING: {
        CandidateState.EXECUTION_VALIDATED,
        CandidateState.WAITING_ENTRY,
        CandidateState.LIMIT_ARMED,
        CandidateState.EXECUTION_REJECTED,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.WAITING_ENTRY: {
        CandidateState.AI_REVIEW_PENDING,
        CandidateState.AI_COHERENT,
        CandidateState.EXECUTION_VALIDATING,
        CandidateState.EXECUTION_VALIDATED,
        CandidateState.LIMIT_ARMED,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.LIMIT_ARMED: {
        CandidateState.RISK_APPROVED,
        CandidateState.RISK_REJECTED,
        CandidateState.ORDER_PENDING,
        CandidateState.OPEN,
        CandidateState.WAITING_ENTRY,
        CandidateState.EXECUTION_VALIDATING,
        CandidateState.EXECUTION_VALIDATED,
        CandidateState.CLOSED,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.EXECUTION_VALIDATED: {
        CandidateState.WAITING_ENTRY,
        CandidateState.RISK_APPROVED,
        CandidateState.RISK_REJECTED,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.RISK_APPROVED: {
        CandidateState.ORDER_PENDING,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.ORDER_PENDING: {
        CandidateState.OPEN,
        CandidateState.PARTIALLY_FILLED,
        CandidateState.CLOSED,
        CandidateState.INVALIDATED,
        CandidateState.EXPIRED,
    },
    CandidateState.PARTIALLY_FILLED: {
        CandidateState.OPEN,
        CandidateState.CLOSED,
        CandidateState.INVALIDATED,
    },
    CandidateState.OPEN: {
        CandidateState.PARTIALLY_FILLED,
        CandidateState.CLOSED,
        CandidateState.INVALIDATED,
    },
}


@dataclass
class CandidateRecord:
    candidate_id: str
    symbol: str
    snapshot_id: str
    event_id: str
    state: CandidateState
    created_at: str
    updated_at: str
    reason_code: str = ""
    reason: str = ""
    version: int = 1
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


class CandidateLifecycleError(ValueError):
    pass


class CandidateLifecycleService:
    """Single in-process authority for candidate state transitions.

    It deliberately does not alter Tri-Core decisions. It records and guards
    downstream state transitions for the exact snapshot/event identity.
    """

    def __init__(self) -> None:
        self._records: dict[str, CandidateRecord] = {}
        self._current_by_symbol: dict[str, str] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def candidate_id(snapshot_id: str, event_id: str) -> str:
        return f"{snapshot_id}:{event_id}"

    @staticmethod
    def _symbol_key(symbol: str) -> str:
        return str(symbol or "").upper().replace("/", "").replace("-", "")

    async def register(
        self,
        *,
        symbol: str,
        snapshot_id: str,
        event_id: str,
        ready: bool,
        replace_current: bool = True,
    ) -> CandidateRecord:
        if not snapshot_id or not event_id:
            raise CandidateLifecycleError("Candidate requires snapshot_id and event_id")
        cid = self.candidate_id(snapshot_id, event_id)
        symbol_key = self._symbol_key(symbol)
        now = datetime.now(timezone.utc).isoformat()

        async with self._lock:
            previous_id = self._current_by_symbol.get(symbol_key)
            if previous_id and previous_id != cid:
                if not replace_current:
                    raise CandidateLifecycleError(
                        f"Candidate {cid} is stale; current candidate is {previous_id}"
                    )
                previous = self._records.get(previous_id)
                if (
                    previous
                    and previous.state not in TERMINAL_STATES
                    and previous.state not in ACTIVE_EXECUTION_STATES
                ):
                    self._apply_transition(
                        previous,
                        CandidateState.INVALIDATED,
                        reason_code="EVENT_REPLACED",
                        reason=f"Replaced by candidate {cid}",
                    )

            record = self._records.get(cid)
            if record is None:
                record = CandidateRecord(
                    candidate_id=cid,
                    symbol=symbol,
                    snapshot_id=snapshot_id,
                    event_id=event_id,
                    state=CandidateState.DETECTED,
                    created_at=now,
                    updated_at=now,
                    history=[{
                        "state": CandidateState.DETECTED.value,
                        "at": now,
                        "reason_code": "REGISTERED",
                    }],
                )
                self._records[cid] = record
                observability.inc(
                    "ai_trade_candidate_state_entries",
                    state=CandidateState.DETECTED.value,
                )
            self._current_by_symbol[symbol_key] = cid

            if ready and record.state == CandidateState.DETECTED:
                self._apply_transition(
                    record,
                    CandidateState.TRI_CORE_READY,
                    reason_code="TRI_CORE_ACTIONABLE",
                )
            return deepcopy(record)

    async def transition(
        self,
        *,
        symbol: str,
        snapshot_id: str,
        event_id: str,
        new_state: CandidateState | str,
        reason_code: str = "",
        reason: str = "",
        require_current: bool = True,
    ) -> bool:
        state = CandidateState(new_state)
        cid = self.candidate_id(snapshot_id, event_id)
        symbol_key = self._symbol_key(symbol)
        async with self._lock:
            record = self._records.get(cid)
            if record is None:
                logger.debug(f"[CandidateLifecycle] Record not found for {cid}")
                return False
            if record.snapshot_id != snapshot_id or record.event_id != event_id:
                logger.debug(f"[CandidateLifecycle] Snapshot/event mismatch for {cid}")
                return False
            if require_current and self._current_by_symbol.get(symbol_key) != cid:
                logger.debug(f"[CandidateLifecycle] Candidate not current for {cid}: current={self._current_by_symbol.get(symbol_key)}")
                return False
            if record.state == state:
                return True
            if record.state in TERMINAL_STATES:
                logger.info(f"[CandidateLifecycle] Rejected transition {record.state} -> {state} for {cid}: already terminal")
                return False
            allowed = ALLOWED_TRANSITIONS.get(record.state, set())
            if state not in allowed:
                logger.info(f"[CandidateLifecycle] Disallowed transition {record.state} -> {state} for {cid} (allowed: {allowed})")
                return False
            self._apply_transition(
                record,
                state,
                reason_code=reason_code,
                reason=reason,
            )
            return True

    async def expire(
        self,
        *,
        symbol: str,
        snapshot_id: str,
        event_id: str,
        reason: str = "",
    ) -> bool:
        return await self.transition(
            symbol=symbol,
            snapshot_id=snapshot_id,
            event_id=event_id,
            new_state=CandidateState.EXPIRED,
            reason_code="SNAPSHOT_EXPIRED",
            reason=reason,
        )

    async def invalidate_current(
        self,
        *,
        symbol: str,
        reason_code: str,
        reason: str = "",
    ) -> bool:
        symbol_key = self._symbol_key(symbol)
        async with self._lock:
            cid = self._current_by_symbol.get(symbol_key)
            record = self._records.get(cid or "")
            if record is None or record.state in TERMINAL_STATES:
                return False
            self._apply_transition(
                record,
                CandidateState.INVALIDATED,
                reason_code=reason_code,
                reason=reason,
            )
            return True

    async def get(
        self,
        *,
        snapshot_id: str,
        event_id: str,
    ) -> dict[str, Any] | None:
        async with self._lock:
            record = self._records.get(self.candidate_id(snapshot_id, event_id))
            return deepcopy(record.to_dict()) if record else None

    async def current(self, symbol: str) -> dict[str, Any] | None:
        async with self._lock:
            cid = self._current_by_symbol.get(self._symbol_key(symbol))
            record = self._records.get(cid or "")
            return deepcopy(record.to_dict()) if record else None

    async def reset(self) -> None:
        async with self._lock:
            self._records.clear()
            self._current_by_symbol.clear()

    @staticmethod
    def _apply_transition(
        record: CandidateRecord,
        state: CandidateState,
        *,
        reason_code: str = "",
        reason: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        previous_state = record.state
        record.state = state
        record.updated_at = now
        record.reason_code = reason_code
        record.reason = reason
        record.version += 1
        record.history.append({
            "state": state.value,
            "at": now,
            "reason_code": reason_code,
            "reason": reason,
        })
        observability.inc(
            "ai_trade_candidate_transitions",
            from_state=previous_state.value,
            to_state=state.value,
            reason_code=reason_code or "none",
        )
        observability.inc(
            "ai_trade_candidate_state_entries",
            state=state.value,
        )


candidate_lifecycle = CandidateLifecycleService()
