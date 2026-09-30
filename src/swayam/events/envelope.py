"""Event envelope.

Design note (docs/phase1_arch.md section 5): the envelope is an immutable frozen
dataclass, while *payloads* and *aggregates* are pydantic models. The split is
intentional -- the envelope is created millions of times and must be cheap and
immutable; payloads are the surface that needs validation, defaults and schema
evolution.

There is no ``updated_at`` on any event, because there is nothing to update. If you
find yourself wanting one, you want a new event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from swayam.core.ids import CorrelationId, EventId, new_id


class ActorType(StrEnum):
    """Who caused this event to exist."""

    USER = "user"
    """A human, normally via a conversational turn."""

    SYSTEM = "system"
    """The platform acting on its own schedule, e.g. a reminder firing."""

    IMPORT = "import"
    """Bulk or third-party ingestion. Phase 1 uses this only for bootstrap."""


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    id: str | None = None

    @classmethod
    def user(cls, user_id: str) -> Actor:
        return cls(type=ActorType.USER, id=user_id)

    @classmethod
    def system(cls) -> Actor:
        return cls(type=ActorType.SYSTEM)

    @classmethod
    def importer(cls, source: str) -> Actor:
        return cls(type=ActorType.IMPORT, id=source)


@dataclass(frozen=True, slots=True)
class Event:
    """An uncommitted intent to record something that happened.

    Produced by the domain, accepted by the event store. Never mutated after
    construction.
    """

    type: str
    payload: dict[str, Any]
    occurred_at: datetime
    actor: Actor
    correlation_id: CorrelationId
    causation_id: UUID | None = None
    """The conversation turn that produced this event. Null for autonomous system
    events such as a reminder firing (invariant I7)."""
    idempotency_key: str | None = None
    event_id: EventId = field(default_factory=lambda: EventId(new_id()))


@dataclass(frozen=True, slots=True)
class RecordedEvent:
    """An event that the store has durably accepted.

    Carries everything the domain needs to replay a stream, plus the store-assigned
    position. Produced only by an ``EventStore.append``.
    """

    event: Event
    stream_id: UUID
    stream_type: str
    seq: int
    """Per-stream sequence, gapless, starting at 1. No global ordering exists."""
    recorded_at: datetime
    version: int
    """Payload schema version at time of write. Historical payloads are never
    rewritten, so an old event keeps the version it was written with."""
