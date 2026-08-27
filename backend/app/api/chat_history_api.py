"""
Chat History API
Persistent chat memory stored in SQLite - sessions grouped by day, messages with timestamps.
Endpoints:
  GET  /api/v1/chat/sessions          - list all sessions (newest first)
  POST /api/v1/chat/sessions          - create a new session
  GET  /api/v1/chat/sessions/today    - get today session (create if not exists)
  GET  /api/v1/chat/sessions/{id}     - get session with messages
  DELETE /api/v1/chat/sessions/{id}   - delete session + messages
  POST /api/v1/chat/sessions/{id}/messages - append a message
  POST /api/v1/chat/messages/bulk-save     - save user+assistant in one call
  GET  /api/v1/chat/context           - last N messages as LLM context window
"""

from __future__ import annotations

import os
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, date
from pathlib import Path
from typing import AsyncGenerator, Literal, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.security import verify_api_key

router = APIRouter()

# Dynamic database path resolution (supports Docker /app/data and local OS)
_data_dir = os.getenv("DATA_DIR")
if _data_dir:
    DB_PATH = Path(_data_dir) / "chat_history.db"
elif Path("/app/data").exists() and os.name != "nt":
    DB_PATH = Path("/app/data/chat_history.db")
else:
    DB_PATH = Path(__file__).parent.parent.parent / "data" / "chat_history.db"

MEMORY_WINDOW = 15  # last N messages sent to LLM as context
_today_session_lock = asyncio.Lock()


@asynccontextmanager
async def _get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON;")
        await db.execute("PRAGMA busy_timeout = 5000;")
        await db.execute("PRAGMA journal_mode = WAL;")
        yield db


# ---------------------------------------------------------------------------
# DB init
# ---------------------------------------------------------------------------

async def init_db() -> None:
    async with _get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id            TEXT PRIMARY KEY,
                title         TEXT NOT NULL,
                day_label     TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                message_count INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id         TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_msg_session ON chat_messages(session_id, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_session_day ON chat_sessions(day_label, created_at)")
        await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _today_label() -> str:
    return date.today().isoformat()

def _thai_day_title(day_label: str) -> str:
    try:
        d = date.fromisoformat(day_label)
        today = date.today()
        delta = (today - d).days
        if delta == 0:
            return f"Today - {d.strftime('%d %b %Y')}"
        elif delta == 1:
            return f"Yesterday - {d.strftime('%d %b %Y')}"
        else:
            return d.strftime('%d %b %Y')
    except Exception:
        return day_label


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class NewSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)

class AppendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16_000)

class BulkSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=100)
    user_content: str = Field(min_length=1, max_length=16_000)
    assistant_content: str = Field(min_length=1, max_length=16_000)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/sessions")
async def list_sessions(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=100_000),
    _key: str = Depends(verify_api_key),
):
    async with _get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        total_cursor = await db.execute("SELECT COUNT(*) FROM chat_sessions")
        total = int((await total_cursor.fetchone())[0])
    sessions = []
    for row in rows:
        s = dict(row)
        s["day_title"] = _thai_day_title(s["day_label"])
        sessions.append(s)
    grouped: dict = {}
    for s in sessions:
        key = s["day_label"]
        grouped.setdefault(key, []).append(s)
    return {
        "total": total,
        "sessions": sessions,
        "grouped": [
            {"day_label": k, "day_title": _thai_day_title(k), "sessions": v}
            for k, v in grouped.items()
        ],
    }


@router.post("/sessions")
async def create_session(req: NewSessionRequest, _key: str = Depends(verify_api_key)):
    session_id = str(uuid.uuid4())
    day_label = _today_label()
    title = req.title or f"Chat {day_label}"
    now = _now_iso()
    async with _get_db() as db:
        await db.execute(
            "INSERT INTO chat_sessions (id, title, day_label, created_at, updated_at, message_count) VALUES (?,?,?,?,?,0)",
            (session_id, title, day_label, now, now),
        )
        await db.commit()
    return {
        "id": session_id, "title": title, "day_label": day_label,
        "day_title": _thai_day_title(day_label),
        "created_at": now, "updated_at": now, "message_count": 0, "messages": [],
    }


@router.get("/sessions/today")
async def get_or_create_today(_key: str = Depends(verify_api_key)):
    day_label = _today_label()
    async with _today_session_lock:
        async with _get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM chat_sessions WHERE day_label=? ORDER BY created_at DESC LIMIT 1", (day_label,)
            )
            row = await cursor.fetchone()
            if row:
                session = dict(row)
                cursor2 = await db.execute(
                    "SELECT * FROM chat_messages WHERE session_id=? ORDER BY created_at ASC, rowid ASC", (session["id"],)
                )
                session["messages"] = [dict(r) for r in await cursor2.fetchall()]
                session["day_title"] = _thai_day_title(session["day_label"])
                return session

            session_id = str(uuid.uuid4())
            now = _now_iso()
            title = f"Chat {day_label}"
            await db.execute(
                "INSERT INTO chat_sessions (id, title, day_label, created_at, updated_at, message_count) VALUES (?,?,?,?,?,0)",
                (session_id, title, day_label, now, now),
            )
            await db.commit()
            return {
                "id": session_id, "title": title, "day_label": day_label,
                "day_title": _thai_day_title(day_label),
                "created_at": now, "updated_at": now, "message_count": 0, "messages": [],
            }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, _key: str = Depends(verify_api_key)):
    async with _get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        session = dict(row)
        cursor2 = await db.execute(
            "SELECT * FROM chat_messages WHERE session_id=? ORDER BY created_at ASC, rowid ASC", (session_id,)
        )
        session["messages"] = [dict(r) for r in await cursor2.fetchall()]
        session["day_title"] = _thai_day_title(session["day_label"])
    return session


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, _key: str = Depends(verify_api_key)):
    async with _get_db() as db:
        cursor = await db.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
        await db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": session_id}


@router.post("/sessions/{session_id}/messages")
async def append_message(session_id: str, req: AppendMessageRequest, _key: str = Depends(verify_api_key)):
    msg_id = str(uuid.uuid4())
    now = _now_iso()
    async with _get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id FROM chat_sessions WHERE id=?", (session_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")
        await db.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (msg_id, session_id, req.role, req.content, now),
        )
        await db.execute(
            "UPDATE chat_sessions SET updated_at=?, message_count=message_count+1 WHERE id=?",
            (now, session_id),
        )
        await db.commit()
    return {"id": msg_id, "session_id": session_id, "role": req.role, "content": req.content, "created_at": now}


@router.post("/messages/bulk-save")
async def bulk_save(req: BulkSaveRequest, _key: str = Depends(verify_api_key)):
    now = _now_iso()
    user_id = str(uuid.uuid4())
    ai_id = str(uuid.uuid4())
    async with _get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT id FROM chat_sessions WHERE id=?", (req.session_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")
        await db.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (user_id, req.session_id, "user", req.user_content, now),
        )
        await db.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (ai_id, req.session_id, "assistant", req.assistant_content, now),
        )
        await db.execute(
            "UPDATE chat_sessions SET updated_at=?, message_count=message_count+2 WHERE id=?",
            (now, req.session_id),
        )
        await db.commit()
    return {"saved": 2, "user_message_id": user_id, "assistant_message_id": ai_id}


@router.get("/context")
async def get_context_window(
    session_id: str = Query(min_length=1, max_length=100),
    window: int = Query(MEMORY_WINDOW, ge=1, le=50),
    _key: str = Depends(verify_api_key),
):
    async with _get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (session_id, window),
        )
        rows = await cursor.fetchall()
    messages = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    return {"session_id": session_id, "window": window, "messages": messages}
