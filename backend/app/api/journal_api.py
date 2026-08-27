"""
Journal API
Trade journaling with notes, screenshots, lessons, and performance stats.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.security import verify_api_key
from app.core.json_store import read_json, update_json

router = APIRouter()

JOURNAL_STORE_FILE = Path(__file__).parent.parent.parent / "config" / "journal_store.json"


def _load_journal() -> dict[str, dict]:
    data = read_json(JOURNAL_STORE_FILE, dict)
    if not isinstance(data, dict):
        raise ValueError("Journal store root must be a JSON object")
    return data


def _mutate_journal(mutator):
    global _journal
    _journal, result = update_json(JOURNAL_STORE_FILE, dict, mutator)
    return result


_journal: dict[str, dict] = _load_journal()


class JournalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_id: Optional[str] = Field(default=None, max_length=100)
    symbol: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_./:-]+$")
    direction: Literal["long", "short"]
    entry: float = Field(gt=0, allow_inf_nan=False)
    stop_loss: float = Field(gt=0, allow_inf_nan=False)
    take_profit: float = Field(gt=0, allow_inf_nan=False)
    close_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    outcome: Optional[Literal["win", "loss", "breakeven"]] = None
    pnl: Optional[float] = Field(default=None, allow_inf_nan=False)
    pnl_pct: Optional[float] = Field(default=None, allow_inf_nan=False)
    timeframe: str = Field(default="1h", max_length=10)
    setup_type: str = Field(default="SMC", max_length=100)
    notes: str = Field(default="", max_length=10_000)
    lessons: str = Field(default="", max_length=10_000)
    confluence_score: int = Field(default=0, ge=0, le=100)
    followed_plan: bool = True
    emotion_rating: int = Field(default=5, ge=1, le=10)
    screenshot_url: Optional[str] = Field(default=None, max_length=2000, pattern=r"^https?://")
    tags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_geometry_and_tags(self):
        valid = (
            self.stop_loss < self.entry < self.take_profit
            if self.direction == "long"
            else self.take_profit < self.entry < self.stop_loss
        )
        if not valid:
            raise ValueError("SL and TP must be on the correct sides of entry")
        self.tags = [tag.strip()[:50] for tag in self.tags if tag.strip()]
        return self


class UpdateJournalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    close_price: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    outcome: Optional[Literal["win", "loss", "breakeven"]] = None
    pnl: Optional[float] = Field(default=None, allow_inf_nan=False)
    pnl_pct: Optional[float] = Field(default=None, allow_inf_nan=False)
    notes: Optional[str] = Field(default=None, max_length=10_000)
    lessons: Optional[str] = Field(default=None, max_length=10_000)
    followed_plan: Optional[bool] = None
    emotion_rating: Optional[int] = Field(default=None, ge=1, le=10)
    screenshot_url: Optional[str] = Field(default=None, max_length=2000, pattern=r"^https?://")
    tags: Optional[list[str]] = Field(default=None, max_length=20)


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
    def mutate(entries: dict[str, dict]) -> dict:
        entries[entry_id] = record
        return dict(record)
    return _mutate_journal(mutate)


@router.get("/entries")
async def list_entries(
    outcome: Optional[Literal["win", "loss", "breakeven"]] = None,
    symbol: Optional[str] = Query(default=None, max_length=30),
    limit: int = Query(50, ge=1, le=500),
    _key: str = Depends(verify_api_key),
):
    """List journal entries with optional filtering."""
    entries = list(_load_journal().values())
    if outcome:
        entries = [e for e in entries if e.get("outcome") == outcome]
    if symbol:
        entries = [e for e in entries if e.get("symbol") == symbol]
    total = len(entries)
    entries = sorted(entries, key=lambda x: x["created_at"], reverse=True)[:limit]
    return {"total": total, "entries": entries}


@router.get("/entries/{entry_id}")
async def get_entry(
    entry_id: str,
    _key: str = Depends(verify_api_key),
):
    """Get a single journal entry."""
    record = _load_journal().get(entry_id)
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
    data = update.model_dump(exclude_none=True)
    if "tags" in data:
        data["tags"] = [str(tag).strip()[:50] for tag in data["tags"] if str(tag).strip()]

    def mutate(entries: dict[str, dict]) -> Optional[dict]:
        record = entries.get(entry_id)
        if not record:
            return None
        record.update(data)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        return dict(record)

    record = _mutate_journal(mutate)
    if not record:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return record


@router.delete("/entries/{entry_id}")
async def delete_entry(
    entry_id: str,
    _key: str = Depends(verify_api_key),
):
    """Delete a journal entry."""
    def mutate(entries: dict[str, dict]) -> bool:
        return entries.pop(entry_id, None) is not None
    if not _mutate_journal(mutate):
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return {"message": "Entry deleted", "id": entry_id}


@router.get("/stats")
async def get_stats(_key: str = Depends(verify_api_key)):
    """Return aggregate performance statistics from the journal."""
    entries = list(_load_journal().values())
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
    rr_values = []
    for entry in entries:
        risk = abs(float(entry.get("entry", 0)) - float(entry.get("stop_loss", 0)))
        reward = abs(float(entry.get("take_profit", 0)) - float(entry.get("entry", 0)))
        if risk > 0:
            rr_values.append(reward / risk)

    return {
        "total": total,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "win_rate": round(wins / closed * 100, 1) if closed > 0 else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_rr": round(sum(rr_values) / len(rr_values), 2) if rr_values else 0,
        "avg_emotion_rating": round(avg_emotion, 1),
        "plan_adherence_pct": round(plan_followed / total * 100, 1),
    }


@router.get("/scorecard")
async def get_trade_scorecard(_key: str = Depends(verify_api_key)):
    """Return evidence-based execution metrics from persisted closed trades."""
    from app.api.trades import get_all_trades

    closed = [trade for trade in get_all_trades().values() if trade.get("status") == "closed"]
    reviewed = [trade for trade in closed if trade.get("execution_rating") is not None]
    wins = [trade for trade in closed if float(trade.get("pnl", 0) or 0) > 0]
    rr_values = []
    for trade in closed:
        entry = float(trade.get("entry", 0) or 0)
        risk = abs(entry - float(trade.get("initial_stop_loss", trade.get("stop_loss", 0)) or 0))
        reward = abs(float(trade.get("take_profit", 0) or 0) - entry)
        if risk > 0:
            rr_values.append(reward / risk)

    followed = [bool(trade.get("followed_plan")) for trade in reviewed if trade.get("followed_plan") is not None]
    ratings = [max(1, min(5, int(trade["execution_rating"]))) for trade in reviewed]
    plan_pct = sum(followed) / len(followed) * 100 if followed else 0.0
    avg_rating = sum(ratings) / len(ratings) if ratings else 0.0
    discipline = round(plan_pct * 0.6 + (avg_rating / 5.0 * 100) * 0.4) if reviewed else 0

    tag_counts: dict[str, int] = {}
    for trade in wins:
        for tag in trade.get("tags", []):
            tag_counts[str(tag)] = tag_counts.get(str(tag), 0) + 1
    best_setups = [tag for tag, _ in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))[:3]]
    return {
        "discipline_score": discipline,
        "plan_adherence_pct": round(plan_pct, 1),
        "avg_execution_rating": round(avg_rating, 2),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
        "avg_rr": round(sum(rr_values) / len(rr_values), 2) if rr_values else 0.0,
        "best_setups": best_setups,
        "closed_trades": len(closed),
        "reviewed_trades": len(reviewed),
    }


@router.post("/entries/{trade_id}/ai-review")
@router.post("/entries/{trade_id}/rule-review")
async def review_closed_trade(trade_id: str, _key: str = Depends(verify_api_key)):
    """Generate a transparent rule-based audit; never label it as an LLM result."""
    from app.api.trades import get_all_trades, update_trade_audit_sync
    from app.services.paper_oms import PaperOMSError, paper_oms

    if paper_oms.ready:
        try:
            trade = await paper_oms.get_position(trade_id)
        except PaperOMSError:
            trade = None
    else:
        trade = get_all_trades().get(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.get("status") != "closed":
        raise HTTPException(status_code=409, detail="Only closed trades can be reviewed")

    reason = str(trade.get("close_reason", "manual")).lower()
    followed_plan = any(token in reason for token in ("take profit", "tp hit", "stop loss", "sl hit", "trailing", "breakeven"))
    rating = 4 if followed_plan else 3
    if float(trade.get("estimated_risk", 0) or 0) <= 0:
        rating = max(1, rating - 1)
    if "take profit" in reason or "tp hit" in reason:
        lesson = "Exit matched the recorded take-profit rule; verify that future entries use the same risk budget."
        tags = ["planned-exit", "take-profit"]
    elif "stop loss" in reason or "sl hit" in reason:
        lesson = "The stop rule was respected; review setup quality without widening the original stop."
        tags = ["planned-exit", "stop-loss"]
    elif "trailing" in reason or "breakeven" in reason:
        lesson = "Protection logic closed the trade; compare the trailing setting with maximum favorable excursion."
        tags = ["protected-exit"]
    else:
        lesson = "Record the concrete reason for the discretionary exit before evaluating plan adherence."
        tags = ["manual-exit"]

    review = (
        f"Rule-based audit: exit={trade.get('close_reason', 'manual')}; "
        f"realized PnL={float(trade.get('pnl', 0) or 0):+.2f}. "
        "This review is based on persisted order fields, not an AI model opinion."
    )
    audit = {
        "ai_review": review,
        "review_source": "deterministic_rules",
        "execution_rating": rating,
        "lessons": lesson,
        "tags": tags,
        "followed_plan": followed_plan,
    }
    if paper_oms.ready:
        try:
            updated = await paper_oms.update_audit(trade_id, audit)
        except PaperOMSError:
            updated = None
    else:
        updated = update_trade_audit_sync(trade_id, audit)
    if not updated:
        raise HTTPException(status_code=409, detail="Trade state changed before review")
    return {"status": "ok", "review_source": "deterministic_rules", "trade": updated}
