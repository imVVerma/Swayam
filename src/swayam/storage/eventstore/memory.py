"""In-memory event store.

Reference implementation of the ``EventStore`` contract. This is not a toy: the mock
runtime runs entirely on it, so any behaviour the conversational layer depends on is
exercised for real. What it does not do is persist, and it serialises all appends
through a single lock -- the Postgres adapter uses per-stream row locks instead, which
is why the contract suite runs against both.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import UUID

from swayam.core.clock import Clock, SystemClock
from swayam.core.errors import ConcurrencyError, ValidationError
from swayam.core.ids import CorrelationId
from swayam.events.catalogue import registry as default_registry
from swayam.events.envelope import Event, RecordedEvent
from swayam.events.registry import EventRegistry
from swayam.storage.eventstore.port import AppendResult, Cursor, StreamPage


class InMemoryEventStore:
    def __init__(self, clock: Clock | None = None, registry: EventRegistry | None = None) -> None:
        self._clock = clock or SystemClock()
        self._registry = registry or default_registry
        self._streams: dict[UUID, list[RecordedEvent]] = {}
        self._log: list[RecordedEvent] = []
        self._idempotency: dict[tuple[UUID, str], AppendResult] = {}
        self._lock = asyncio.Lock()

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
        if not events:
            raise ValidationError("cannot append an empty batch")

        if idempotency_key is not None:
            prior = self._idempotency.get((correlation_id, idempotency_key))
            if prior is not None:
                return replace(prior, deduplicated=True)

        async with self._lock:
            existing = self._streams.get(stream_id, [])
            current = existing[-1].seq if existing else 0
            if current != expected_version:
                raise ConcurrencyError(
                    f"stream {stream_id} is at version {current}, expected {expected_version}"
                )

            now = self._clock.now()
            recorded: list[RecordedEvent] = []
            for offset, event in enumerate(events, start=1):
                event_type = self._registry.get(event.type)
                normalised = self._registry.validate(event.type, event.payload)
                recorded.append(
                    RecordedEvent(
                        event=replace(event, payload=normalised),
                        stream_id=stream_id,
                        stream_type=stream_type,
                        seq=current + offset,
                        recorded_at=now,
                        version=event_type.version,
                    )
                )

            self._streams[stream_id] = [*existing, *recorded]
            self._log.extend(recorded)

            result = AppendResult(
                stream_id=stream_id,
                previous_version=current,
                new_version=current + len(recorded),
                events=tuple(recorded),
            )
            if idempotency_key is not None:
                self._idempotency[(correlation_id, idempotency_key)] = result
            return result

    async def read_stream(self, stream_id: UUID, *, from_seq: int = 0) -> tuple[RecordedEvent, ...]:
        return tuple(e for e in self._streams.get(stream_id, ()) if e.seq > from_seq)

    async def stream_version(self, stream_id: UUID) -> int:
        existing = self._streams.get(stream_id)
        return existing[-1].seq if existing else 0

    async def read_all(
        self, *, after_cursor: Cursor | None = None, limit: int = 1000
    ) -> StreamPage:
        start = 0 if after_cursor is None else after_cursor + 1
        window = self._log[start : start + limit]
        next_cursor: Cursor | None = None
        # Strictly less-than: a full page that happens to end the log must report end
        # of log, not a cursor that would re-scan it. This is the off-by-one that
        # silently drops the last event of every exactly-full page.
        if window and start + len(window) < len(self._log):
            next_cursor = start + len(window) - 1
        return StreamPage(events=tuple(window), next_cursor=next_cursor)

    async def all_events(self) -> tuple[RecordedEvent, ...]:
        """Test helper: the entire log in append order."""
        return tuple(self._log)
