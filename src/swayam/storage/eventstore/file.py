"""File-backed event store (JSONL).

Persists events to a JSONL file and replays them on startup. Implements the same
``EventStore`` protocol as ``InMemoryEventStore`` so the rest of the system is
unaware of the persistence layer.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import UUID

from swayam.core.clock import Clock, SystemClock
from swayam.core.errors import ConcurrencyError, ValidationError
from swayam.core.ids import CorrelationId
from swayam.events.catalogue import registry as default_registry
from swayam.events.envelope import Actor, ActorType, Event, RecordedEvent
from swayam.events.registry import EventRegistry
from swayam.storage.eventstore.port import AppendResult, Cursor, StreamPage


class FileEventStore:
    def __init__(
        self,
        path: Path,
        *,
        clock: Clock | None = None,
        registry: EventRegistry | None = None,
    ) -> None:
        self._path = path
        self._clock = clock or SystemClock()
        self._registry = registry or default_registry
        self._streams: dict[UUID, list[RecordedEvent]] = {}
        self._log: list[RecordedEvent] = []
        self._idempotency: dict[tuple[UUID, str], AppendResult] = {}
        self._lock = asyncio.Lock()
        self._replayed = False

    async def _ensure_replayed(self) -> None:
        if self._replayed:
            return
        self._replayed = True
        if not self._path.exists():
            return
        async with self._lock:
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    event_data = data["event"]
                    recorded = RecordedEvent(
                        event=Event(
                            type=event_data["type"],
                            payload=event_data["payload"],
                            occurred_at=datetime.fromisoformat(event_data["occurred_at"]),
                            actor=Actor(
                                type=ActorType(event_data["actor"]["type"]),
                                id=event_data["actor"].get("id"),
                            ),
                            causation_id=UUID(event_data["causation_id"])
                            if event_data.get("causation_id")
                            else None,
                            correlation_id=CorrelationId(UUID(event_data["correlation_id"])),
                        ),
                        stream_id=UUID(data["stream_id"]),
                        stream_type=data["stream_type"],
                        seq=data["seq"],
                        recorded_at=datetime.fromisoformat(data["recorded_at"]),
                        version=data["version"],
                    )
                    self._streams.setdefault(recorded.stream_id, []).append(recorded)
                    self._log.append(recorded)

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

        await self._ensure_replayed()

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

            await self._persist(recorded)
            return result

    async def _persist(self, events: list[RecordedEvent]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            for recorded in events:
                event_type = self._registry.get(recorded.event.type)
                line = json.dumps(
                    {
                        "event": {
                            "type": recorded.event.type,
                            "payload": recorded.event.payload,
                            "occurred_at": recorded.event.occurred_at.isoformat(),
                            "actor": {
                                "type": recorded.event.actor.type.value,
                                "id": recorded.event.actor.id,
                            },
                            "causation_id": str(recorded.event.causation_id)
                            if recorded.event.causation_id
                            else None,
                            "correlation_id": str(recorded.event.correlation_id),
                            "version": event_type.version,
                        },
                        "stream_id": str(recorded.stream_id),
                        "stream_type": recorded.stream_type,
                        "seq": recorded.seq,
                        "recorded_at": recorded.recorded_at.isoformat(),
                        "version": recorded.version,
                    },
                    separators=(",", ":"),
                )
                f.write(line + "\n")

    async def read_stream(self, stream_id: UUID, *, from_seq: int = 0) -> tuple[RecordedEvent, ...]:
        await self._ensure_replayed()
        return tuple(e for e in self._streams.get(stream_id, ()) if e.seq > from_seq)

    async def stream_version(self, stream_id: UUID) -> int:
        await self._ensure_replayed()
        existing = self._streams.get(stream_id)
        return existing[-1].seq if existing else 0

    async def read_all(
        self, *, after_cursor: Cursor | None = None, limit: int = 1000
    ) -> StreamPage:
        await self._ensure_replayed()
        start = 0 if after_cursor is None else after_cursor + 1
        window = self._log[start : start + limit]
        next_cursor: Cursor | None = None
        if window and start + len(window) < len(self._log):
            next_cursor = start + len(window) - 1
        return StreamPage(events=tuple(window), next_cursor=next_cursor)

    async def all_events(self) -> tuple[RecordedEvent, ...]:
        await self._ensure_replayed()
        return tuple(self._log)