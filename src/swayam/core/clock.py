"""Clock abstraction.

The framework requires distinguishing *when something happened* from *when the system
learned of it* (docs/phase1_arch.md section 5.1). Every component that needs the
current time takes a ``Clock`` so tests can pin time instead of racing it.
"""

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current wall-clock time, always timezone-aware in UTC."""
        ...


class SystemClock:
    """Production clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Test clock. Advances only when explicitly told to."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, *, seconds: float = 0, days: float = 0) -> None:
        from datetime import timedelta

        self._now += timedelta(seconds=seconds, days=days)
