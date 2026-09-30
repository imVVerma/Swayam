"""Event store port.

The store is the only component that knows events exist as rows. It validates payload
shape via the registry, allocates per-stream sequence numbers, enforces optimistic
concurrency, and de-duplicates retries. It knows nothing about tasks, projects or
conversation.

Two implementations ship in Phase 1: this contract is run against both the in-memory
adapter (fast, used by the mock runtime and by every unit test) and the Postgres
adapter (``tests/contract`` runs the same suite against both).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from swayam.core.ids import CorrelationId
from swayam.events.envelope import Event, RecordedEvent


@dataclass(frozen=True, slots=True)
class AppendResult:
    stream_id: UUID
    previous_version: int
    new_version: int
    events: tuple[RecordedEvent, ...]
    deduplicated: bool = False
    """True when an idempotency key matched a prior append and nothing was written.
    A retried mutation returns the original events rather than duplicating them."""


@dataclass(frozen=True, slots=True)
class StreamPage:
    """A page of the global log, for projection consumption."""

    events: tuple[RecordedEvent, ...]
    next_cursor: int | None
    """Opaque checkpoint. Pass back as ``after_cursor``. ``None`` means end of log."""


Cursor = int


@runtime_checkable
class EventStore(Protocol):
    async def append(
        self,
        *,
        stream_id: UUID,
        stream_type: str,
        expected_version: int,
        events: tuple[Event, ...],
        correlation_id: CorrelationId,
        idempotency_key: str | None = None,
    ) -> AppendResult:
        """Append ``events`` to ``stream_id`` atomically.

        Raises ``ConcurrencyError`` if the stream is not at ``expected_version``.
        """
        ...

    async def read_stream(
        self, stream_id: UUID, *, from_seq: int = 0
    ) -> tuple[RecordedEvent, ...]:
        """Events for one stream, ascending by ``seq``."""
        ...

    async def stream_version(self, stream_id: UUID) -> int:
        """Current version; 0 for a stream that does not exist."""
        ...

    async def read_all(
        self, *, after_cursor: Cursor | None = None, limit: int = 1000
    ) -> StreamPage:
        """Global log in append order.

        Ordered by a gapless internal offset, not by ``recorded_at`` -- two events
        committed in the same append share a timestamp, so timestamps cannot be a
        reliable total order.
        """
        ...
