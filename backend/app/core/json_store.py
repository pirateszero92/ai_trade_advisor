"""Small cross-process, atomic JSON store for local single-node deployments."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

_registry_guard = threading.Lock()
_thread_locks: dict[str, threading.RLock] = {}


class JsonStoreCorruptionError(RuntimeError):
    pass


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _registry_guard:
        return _thread_locks.setdefault(key, threading.RLock())


@contextmanager
def _process_lock(path: Path):
    # Lock files live outside bind-mounted configuration directories.  This
    # avoids stale host ACL/ownership problems while preserving one stable lock
    # name for all processes on the same node.
    lock_id = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    lock_dir = Path(tempfile.gettempdir()) / "ai_trade_advisor_json_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{lock_id}.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_unlocked(path: Path, default: T) -> T:
    if not path.exists():
        return default
    try:
        # ``utf-8-sig`` is backward-compatible with plain UTF-8 and also
        # accepts legacy JSON files written with a UTF-8 BOM.
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise JsonStoreCorruptionError(f"Invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to read {path}: {exc}") from exc


def _write_unlocked(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        # NamedTemporaryFile retries thousands of names after PermissionError
        # on Windows. A UUID path has negligible collision risk and surfaces
        # ACL/read-only failures immediately.
        with temp_path.open("x", encoding="utf-8") as temp:
            json.dump(value, temp, indent=2, ensure_ascii=False, allow_nan=False)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def read_json(path: Path, default_factory: Callable[[], T]) -> T:
    with _thread_lock(path), _process_lock(path):
        return _read_unlocked(path, default_factory())


def write_json(path: Path, value: object) -> None:
    with _thread_lock(path), _process_lock(path):
        _write_unlocked(path, value)


def update_json(
    path: Path,
    default_factory: Callable[[], T],
    mutator: Callable[[T], R],
) -> tuple[T, R]:
    """Lock, load, mutate and atomically save one JSON document."""
    with _thread_lock(path), _process_lock(path):
        value = _read_unlocked(path, default_factory())
        result = mutator(value)
        # If mutator returned a replacement container of the same type, write that
        to_write = result if (result is not None and isinstance(result, (dict, list)) and isinstance(value, (dict, list)) and not isinstance(result, tuple)) else value
        _write_unlocked(path, to_write)
        return to_write, result

