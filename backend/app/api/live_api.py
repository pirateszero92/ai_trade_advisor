"""Live gateway session controls.

This router is the only entry point that can unlock real-money mutations.
Market analysis and paper trading never need or receive a live session token.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from app.api.trades import InnovestXCancelRequest, InnovestXOrderRequest
from app.core.config import get_settings
from app.core.live_session import LIVE_SESSION_HEADER, LiveSession, live_session_manager, require_live_session
from app.core.security import is_securely_configured, verify_api_key
from app.engines.innovestx_client import InnovestXClient


router = APIRouter()


class OpenLiveSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    broker: Literal["innovestx"] = "innovestx"
    confirmation: Literal["ENABLE_LIVE_TRADING"]
    ttl_minutes: int = Field(default=15, ge=1, le=60)


class KillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["DISABLE_LIVE_TRADING"]


async def _preflight_broker(broker: str) -> dict:
    cfg = get_settings()
    if broker == "innovestx":
        key = (cfg.innovestx_api_key or "").strip()
        secret = (cfg.innovestx_api_secret or "").strip()
        if not key or not secret:
            raise HTTPException(status_code=409, detail="InnovestX credentials are not configured")
        result = await InnovestXClient(api_key=key, api_secret=secret).test_connection()
        if not isinstance(result, dict) or result.get("connected") is not True:
            message = (
                result.get("message") or result.get("error")
                if isinstance(result, dict)
                else None
            )
            raise HTTPException(status_code=503, detail=message or "InnovestX preflight failed")
        return {"broker": broker, "connected": True, "status": result.get("status", "online")}
    raise HTTPException(status_code=409, detail=f"Broker '{broker}' is not live-enabled")


@router.post("/session")
async def open_live_session(
    req: OpenLiveSessionRequest,
    api_key: str = Depends(verify_api_key),
):
    """Run broker preflight and issue an opaque, process-local live token."""
    if api_key == "dev" or not is_securely_configured():
        raise HTTPException(
            status_code=503,
            detail="Live Trading requires a securely configured APP_SECRET_KEY; development auth bypass is not accepted",
        )
    preflight = await _preflight_broker(req.broker)
    token, session = live_session_manager.issue(
        broker=req.broker,
        api_key=api_key,
        ttl_minutes=req.ttl_minutes,
    )
    logger.warning(
        "LIVE SESSION OPENED id={} broker={} expires_at={} actor={}",
        session.session_id,
        session.broker,
        session.expires_at.isoformat(),
        session.api_key_fingerprint,
    )
    return {
        "status": "active",
        "mode": "live",
        "session_token": token,
        "session_id": session.session_id,
        "broker": session.broker,
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "preflight": preflight,
        "capabilities": {
            "account_read": True,
            "order_cancel": True,
            "order_place": False,
            "reason": "Broker-side protective-order OMS is not implemented yet",
        },
    }


@router.get("/session")
async def get_live_session_status(
    _key: str = Depends(verify_api_key),
    token: str | None = Header(default=None, alias=LIVE_SESSION_HEADER),
):
    session = live_session_manager.get(token, api_key=_key)
    if session is None:
        return {"status": "inactive", "mode": "paper"}
    return {
        "status": "active",
        "mode": "live",
        "session_id": session.session_id,
        "broker": session.broker,
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
    }


@router.delete("/session")
async def close_live_session(
    _key: str = Depends(verify_api_key),
    token: str | None = Header(default=None, alias=LIVE_SESSION_HEADER),
):
    session = live_session_manager.get(token, api_key=_key)
    revoked = live_session_manager.revoke(token) if session else False
    if session:
        logger.warning("LIVE SESSION CLOSED id={} broker={}", session.session_id, session.broker)
    return {"status": "inactive", "mode": "paper", "revoked": revoked}


@router.post("/kill-switch")
async def activate_live_kill_switch(
    req: KillSwitchRequest,
    _key: str = Depends(verify_api_key),
):
    """Revoke every live session. Existing broker positions are not closed."""
    revoked = live_session_manager.revoke_all()
    logger.critical("LIVE KILL SWITCH ACTIVATED revoked_sessions={}", revoked)
    return {
        "status": "live_disabled",
        "mode": "paper",
        "revoked_sessions": revoked,
        "note": "No broker positions were closed; protective orders remain the broker's responsibility",
    }


@router.get("/account")
async def get_live_account(
    api_key: str = Depends(verify_api_key),
    session: LiveSession = Depends(require_live_session),
    token: str | None = Header(default=None, alias=LIVE_SESSION_HEADER),
):
    """Return only the account authorized by the current Live Session."""
    from app.api import trades

    return await trades.get_account_portfolio(
        mode="live",
        broker=session.broker,
        _key=api_key,
        live_session_token=token,
    )


@router.post("/orders/innovestx")
async def place_live_innovestx_order(
    req: InnovestXOrderRequest,
    api_key: str = Depends(verify_api_key),
    session: LiveSession = Depends(require_live_session),
):
    """Canonical real-money route; currently fails closed before the broker."""
    from app.api import trades

    return await trades.place_innovestx_order(req=req, _key=api_key, session=session)


@router.post("/orders/innovestx/cancel")
async def cancel_live_innovestx_order(
    req: InnovestXCancelRequest,
    api_key: str = Depends(verify_api_key),
    session: LiveSession = Depends(require_live_session),
):
    from app.api import trades

    return await trades.cancel_innovestx_order(req=req, _key=api_key, session=session)
