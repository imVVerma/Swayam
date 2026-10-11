"""Composition root.

The single place where concrete adapters are chosen. Everything else in the codebase
receives its dependencies, which is what keeps ``domain/`` importable without a
database and keeps the mock runtime a swap rather than a fork.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from swayam.core.clock import Clock, SystemClock
from swayam.events.catalogue import registry as default_registry
from swayam.events.registry import EventRegistry
from swayam.storage.eventstore.file import FileEventStore
from swayam.storage.eventstore.memory import InMemoryEventStore
from swayam.storage.eventstore.port import EventStore


@dataclass(frozen=True, slots=True)
class Storage:
    """Event log plus the read models derived from it."""

    events: EventStore
    clock: Clock
    registry: EventRegistry


def build_storage(
    *,
    clock: Clock | None = None,
    registry: EventRegistry | None = None,
    path: Path | None = None,
) -> Storage:
    """Build storage. Uses file-backed store if ``path`` is provided, else in-memory.

    The Postgres adapter slots in at the same seam once M3 needs durability; nothing
    above ``storage/`` changes when it does.
    """
    resolved_clock = clock or SystemClock()
    resolved_registry = registry or default_registry
    if path is not None:
        events: EventStore = FileEventStore(path, clock=resolved_clock, registry=resolved_registry)
    else:
        events = InMemoryEventStore(clock=resolved_clock, registry=resolved_registry)
    return Storage(
        events=events,
        clock=resolved_clock,
        registry=resolved_registry,
    )
