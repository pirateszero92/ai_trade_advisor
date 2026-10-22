"""Append-only audit trace with bounded hot storage and durable archives."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.services.evidence import fingerprint


TRACE_SCHEMA_VERSION = 1
DEFAULT_HOT_RECORD_LIMIT = 200
DEFAULT_ARCHIVE_RETENTION_DAYS = 365


class DecisionTraceService:
    """Record auditable facts without fabricating unavailable values.

    Recent candidates remain in a bounded JSON hot store for fast updates.
    Evicted records are appended to daily NDJSON archives so forensic history is
    retained without rewriting an ever-growing JSON document on every event.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        archive_dir: Path | None = None,
        hot_record_limit: int = DEFAULT_HOT_RECORD_LIMIT,
        archive_retention_days: int | None = DEFAULT_ARCHIVE_RETENTION_DAYS,
    ) -> None:
        self.path = path or Path(__file__).resolve().parents[2] / "data" / "decision_traces.json"
        self.archive_dir = archive_dir or self.path.parent / "decision_trace_archive"
        self.hot_record_limit = max(int(hot_record_limit), 1)
        self.archive_retention_days = (
            max(int(archive_retention_days), 1)
            if archive_retention_days is not None
            else None
        )
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._loaded = False
        self._dirty = False
        self._flush_task: asyncio.Task | None = None
        self._archived_ids: set[str] | None = None

    @staticmethod
    def candidate_id(snapshot_id: str, event_id: str) -> str:
        return f"{snapshot_id}:{event_id}"

    def _schedule_debounced_save(self) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
            self._flush_task = loop.create_task(self._delayed_save())
        except RuntimeError:
            pass

    async def _delayed_save(self) -> None:
        await asyncio.sleep(2.0)
        await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            await asyncio.to_thread(self._write_sync)

    async def record(
        self,
        *,
        snapshot_id: str,
        event_id: str,
        symbol: str,
        stage: str,
        facts: dict[str, Any],
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if not snapshot_id or not event_id or not stage:
            return {"status": "skipped"}
        cid = self.candidate_id(snapshot_id, event_id)
        now = occurred_at or datetime.now(timezone.utc).isoformat()
        await self._ensure_loaded()
        clean = self._clean(facts)
        fact_hash = fingerprint({"stage": stage, "facts": clean})
        event = {
            "stage": stage,
            "occurred_at": now,
            "facts": clean,
            "fact_hash": fact_hash,
        }
        event["event_hash"] = fingerprint(event)

        async with self._lock:
            record = self._records.get(cid)
            if record is None and self._archived_ids is not None and cid in self._archived_ids:
                record = await asyncio.to_thread(self._read_archived_sync, cid)
                if record is not None:
                    self._records[cid] = record
            if record is None:
                record = {
                    "schema_version": TRACE_SCHEMA_VERSION,
                    "candidate_id": cid,
                    "snapshot_id": snapshot_id,
                    "event_id": event_id,
                    "symbol": symbol,
                    "created_at": now,
                    "updated_at": now,
                    "events": [],
                }
                self._records[cid] = record
            if any(existing.get("fact_hash") == fact_hash for existing in record["events"]):
                return deepcopy(record)
            record["events"].append(event)
            record["updated_at"] = now
            record["trace_hash"] = fingerprint({
                "candidate_id": cid,
                "events": record["events"],
            })
            if len(self._records) > self.hot_record_limit + 50:
                await self._trim_hot_store_locked()
            self._dirty = True
            self._schedule_debounced_save()
            return deepcopy(record)

    async def get(self, *, snapshot_id: str, event_id: str) -> dict[str, Any] | None:
        await self._ensure_loaded()
        cid = self.candidate_id(snapshot_id, event_id)
        async with self._lock:
            record = self._records.get(cid)
            if record is not None:
                return deepcopy(record)
            if self._archived_ids is not None and cid not in self._archived_ids:
                return None
            archived = await asyncio.to_thread(self._read_archived_sync, cid)
            return deepcopy(archived) if archived else None

    async def list_records(self) -> list[dict[str, Any]]:
        """Return all known records; archive expansion is transient, not cached."""
        await self._ensure_loaded()
        await self.flush()
        async with self._lock:
            archived = await asyncio.to_thread(self._read_all_archived_sync)
            archived.update(self._records)
            return [
                deepcopy(record)
                for _, record in sorted(archived.items())
            ]

    async def reset(self) -> None:
        async with self._lock:
            self._records.clear()
            self._dirty = True
            self._loaded = True
            await asyncio.to_thread(self._write_sync)

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            self._records = await asyncio.to_thread(self._read_sync)
            await asyncio.to_thread(self._purge_expired_archives_sync)
            self._archived_ids = await asyncio.to_thread(self._load_archived_ids_sync)
            if len(self._records) > self.hot_record_limit:
                await self._trim_hot_store_locked()
                await asyncio.to_thread(self._write_sync)
            self._loaded = True

    def _load_archived_ids_sync(self) -> set[str]:
        ids: set[str] = set()
        if not self.archive_dir.exists():
            return ids
        for archive_path in self.archive_dir.glob("decision-traces-*.ndjson"):
            try:
                for line in archive_path.read_text(encoding="utf-8").splitlines():
                    if not line:
                        continue
                    try:
                        env = json.loads(line)
                        cid = str(env.get("candidate_id") or "")
                        if cid:
                            ids.add(cid)
                    except Exception:
                        continue
            except Exception:
                continue
        return ids

    async def _trim_hot_store_locked(self) -> None:
        overflow = len(self._records) - self.hot_record_limit
        if overflow <= 0:
            return
        ordered = sorted(
            self._records.items(),
            key=lambda item: (
                str(item[1].get("updated_at") or ""),
                item[0],
            ),
        )
        for cid, record in ordered[:overflow]:
            await asyncio.to_thread(self._archive_record_sync, record)
            if self._archived_ids is not None:
                self._archived_ids.add(cid)
            self._records.pop(cid, None)

    @classmethod
    def _clean(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): cls._clean(item)
                for key, item in value.items()
                if item is not None
            }
        if isinstance(value, (list, tuple)):
            return [cls._clean(item) for item in value if item is not None]
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        if hasattr(value, "item"):
            return value.item()
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def verify(self, record: dict[str, Any]) -> list[str]:
        failures: list[str] = []
        events = list(record.get("events", []) or [])
        for index, event in enumerate(events):
            expected_fact_hash = fingerprint({
                "stage": event.get("stage"),
                "facts": event.get("facts", {}),
            })
            if expected_fact_hash != event.get("fact_hash"):
                failures.append(f"event_{index}_fact_hash_mismatch")
            expected_event_hash = fingerprint({
                "stage": event.get("stage"),
                "occurred_at": event.get("occurred_at"),
                "facts": event.get("facts", {}),
                "fact_hash": event.get("fact_hash"),
            })
            if expected_event_hash != event.get("event_hash"):
                failures.append(f"event_{index}_event_hash_mismatch")
        expected_trace_hash = fingerprint({
            "candidate_id": record.get("candidate_id"),
            "events": events,
        })
        if expected_trace_hash != record.get("trace_hash"):
            failures.append("trace_hash_mismatch")
        return failures

    def _read_sync(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
            return {}

    def _write_sync(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    self._records,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            from loguru import logger
            logger.warning(f"[DecisionTrace] Unable to write hot trace store {self.path}: {exc}")

    @staticmethod
    def _archive_date(record: dict[str, Any]) -> str:
        raw = str(record.get("updated_at") or "")
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            return datetime.now(timezone.utc).date().isoformat()

    def _archive_record_sync(self, record: dict[str, Any]) -> None:
        try:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            record_copy = deepcopy(record)
            envelope = {
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "candidate_id": str(record_copy.get("candidate_id") or ""),
                "record_hash": fingerprint(record_copy),
                "record": record_copy,
            }
            envelope["envelope_hash"] = fingerprint({
                "archived_at": envelope["archived_at"],
                "candidate_id": envelope["candidate_id"],
                "record_hash": envelope["record_hash"],
            })
            archive_path = self.archive_dir / f"decision-traces-{self._archive_date(record_copy)}.ndjson"
            with archive_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(
                    envelope,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ))
                handle.write("\n")
            self._purge_expired_archives_sync()
        except OSError as exc:
            from loguru import logger
            logger.warning(f"[DecisionTrace] Unable to archive trace record: {exc}")

    def _purge_expired_archives_sync(self) -> None:
        if self.archive_retention_days is None or not self.archive_dir.exists():
            return
        cutoff = datetime.now(timezone.utc).date().toordinal() - self.archive_retention_days
        for archive_path in self.archive_dir.glob("decision-traces-*.ndjson"):
            stamp = archive_path.stem.replace("decision-traces-", "")
            try:
                archive_date = datetime.fromisoformat(stamp).date()
            except ValueError:
                continue
            if archive_date.toordinal() < cutoff:
                try:
                    archive_path.unlink()
                except OSError:
                    continue

    @staticmethod
    def _valid_archive_envelope(envelope: Any) -> dict[str, Any] | None:
        if not isinstance(envelope, dict):
            return None
        record = envelope.get("record")
        if not isinstance(record, dict):
            return None
        if fingerprint(record) != envelope.get("record_hash"):
            return None
        expected = fingerprint({
            "archived_at": envelope.get("archived_at"),
            "candidate_id": envelope.get("candidate_id"),
            "record_hash": envelope.get("record_hash"),
        })
        if expected != envelope.get("envelope_hash"):
            return None
        if str(record.get("candidate_id") or "") != str(envelope.get("candidate_id") or ""):
            return None
        return record

    def _read_archived_sync(self, candidate_id: str) -> dict[str, Any] | None:
        if not self.archive_dir.exists():
            return None
        for archive_path in sorted(self.archive_dir.glob("decision-traces-*.ndjson"), reverse=True):
            try:
                lines = archive_path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            for line in reversed(lines):
                try:
                    envelope = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(envelope.get("candidate_id") or "") != candidate_id:
                    continue
                record = self._valid_archive_envelope(envelope)
                if record is not None:
                    return record
        return None

    def _read_all_archived_sync(self) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        if not self.archive_dir.exists():
            return records
        for archive_path in sorted(self.archive_dir.glob("decision-traces-*.ndjson")):
            try:
                lines = archive_path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            for line in lines:
                try:
                    envelope = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record = self._valid_archive_envelope(envelope)
                if record is not None:
                    records[str(record.get("candidate_id") or "")] = record
        return records


decision_trace = DecisionTraceService()
