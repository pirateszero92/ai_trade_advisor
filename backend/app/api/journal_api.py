"""
Journal API
Trade journaling with notes, screenshots, lessons, and performance stats.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.security import verify_api_key

router = APIRouter()

JOURNAL_STORE_FILE = Path(__file__).parent.parent.parent / "config" / "journal_store.json"


def _load_journal() -> dict[str, dict]:
    if JOURNAL_STORE_FILE.exists():
        try:
            return json.loads(JOURNAL_STORE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_journal():
    try:
        JOURNAL_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        JOURNAL_STORE_FILE.write_text(json.dumps(_journal, indent=2), encoding="utf-8")
    except Exception:
        pass


_journal: dict[str, dict] = _load_journal()


class JournalEntry(BaseModel):
    trade_id: Optional[str] = None
    symbol: str
    direction: Literal["long", "short"]
    entry: float
    stop_loss: float
    take_profit: float
    close_price: Optional[float] = None
    outcome: Optional[Literal["win", "loss", "breakeven"]] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    timeframe: str = "1H"
    setup_type: str = "SMC"
    notes: str = ""
    lessons: str = ""
    confluence_score: int = 0
    followed_plan: bool = True
    emotion_rating: int = 5  # 1-10 discipline rating
    screenshot_url: Optional[str] = None
    tags: list[str] = []


class UpdateJournalEntry(BaseModel):
    close_price: Optional[float] = None
    outcome: Optional[Literal["win", "loss", "breakeven"]] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    notes: Optional[str] = None
    lessons: Optional[str] = None
    followed_plan: Optional[bool] = None
    emotion_rating: Optional[int] = None
    screenshot_url: Optional[str] = None
    tags: Optional[list[str]] = None


@router.post("/entries")
async def create_entry(
    entry: JournalEntry,
    _key: str = Depends(verify_api_key),
):
    """Create a new journal entry."""
    entry_id = str(uuid4())
    record = {
        "id": entry_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **entry.model_dump(),
    }
    _journal[entry_id] = record
    _save_journal()
    return record


@router.get("/entries")
async def list_entries(
    outcome: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    _key: str = Depends(verify_api_key),
):
    """List journal entries with optional filtering."""
    entries = list(_journal.values())
    if outcome:
        entries = [e for e in entries if e.get("outcome") == outcome]
    if symbol:
        entries = [e for e in entries if e.get("symbol") == symbol]
    entries = sorted(entries, key=lambda x: x["created_at"], reverse=True)[:limit]
    return {"total": len(entries), "entries": entries}


@router.get("/entries/{entry_id}")
async def get_entry(
    entry_id: str,
    _key: str = Depends(verify_api_key),
):
    """Get a single journal entry."""
    record = _journal.get(entry_id)
    if not record:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return record


@router.patch("/entries/{entry_id}")
async def update_entry(
    entry_id: str,
    update: UpdateJournalEntry,
    _key: str = Depends(verify_api_key),
):
    """Update a journal entry (e.g. add close price, outcome, notes)."""
    record = _journal.get(entry_id)
    if not record:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    data = update.model_dump(exclude_none=True)
    record.update(data)
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    _journal[entry_id] = record
    _save_journal()
    return record


@router.delete("/entries/{entry_id}")
async def delete_entry(
    entry_id: str,
    _key: str = Depends(verify_api_key),
):
    """Delete a journal entry."""
    if entry_id not in _journal:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    del _journal[entry_id]
    _save_journal()
    return {"message": "Entry deleted", "id": entry_id}


@router.get("/stats")
async def get_stats(_key: str = Depends(verify_api_key)):
    """Return aggregate performance statistics from the journal."""
    entries = list(_journal.values())
    total = len(entries)
    if total == 0:
        return {"total": 0, "win_rate": 0, "avg_rr": 0, "total_pnl": 0}

    wins = sum(1 for e in entries if e.get("outcome") == "win")
    losses = sum(1 for e in entries if e.get("outcome") == "loss")
    breakevens = sum(1 for e in entries if e.get("outcome") == "breakeven")
    closed = wins + losses + breakevens

    total_pnl = sum(e.get("pnl") or 0 for e in entries)
    avg_emotion = sum(e.get("emotion_rating") or 5 for e in entries) / total
    plan_followed = sum(1 for e in entries if e.get("followed_plan"))

    return {
        "total": total,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "win_rate": round(wins / closed * 100, 1) if closed > 0 else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_emotion_rating": round(avg_emotion, 1),
        "plan_adherence_pct": round(plan_followed / total * 100, 1),
    }
