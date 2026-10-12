"""
Domain Clock Contract (CORE-001)
Enforces explicit, UTC-aware timekeeping. Prevents hidden system clock dependencies
and guarantees deterministic replay capability.
"""

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """Protocol for abstracting time providers across live, test, and replay modes."""

    def now(self) -> datetime:
        """Returns the current UTC datetime."""
        ...


class SystemClock:
    """Standard system clock for real-time production and paper operations."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """Deterministic frozen clock for unit tests and reproducible state machines."""

    def __init__(self, fixed_time: datetime | None = None) -> None:
        self._current_time = fixed_time or datetime.now(timezone.utc)

    def now(self) -> datetime:
        return self._current_time

    def set_time(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            self._current_time = new_time.replace(tzinfo=timezone.utc)
        else:
            self._current_time = new_time.astimezone(timezone.utc)

    def advance(self, seconds: float = 0.0) -> None:
        from datetime import timedelta
        self._current_time += timedelta(seconds=seconds)


class ReplayClock:
    """Replay clock driven by bar timestamps in historical backtesting."""

    def __init__(self, initial_time: datetime) -> None:
        self._current_time = (
            initial_time if initial_time.tzinfo is not None
            else initial_time.replace(tzinfo=timezone.utc)
        )

    def now(self) -> datetime:
        return self._current_time

    def update_to_bar(self, bar_close_time: datetime) -> None:
        self._current_time = (
            bar_close_time if bar_close_time.tzinfo is not None
            else bar_close_time.replace(tzinfo=timezone.utc)
        )

    def set_time(self, new_time: datetime) -> None:
        self.update_to_bar(new_time)
