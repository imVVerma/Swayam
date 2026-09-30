"""Event store: the port and its in-memory adapter.

Deliberately unaware of the composition root -- importing ``swayam.storage.bootstrap``
from here creates a cycle and, more importantly, would let the event log depend on how
the application happens to be wired.
"""

from swayam.storage.eventstore.memory import InMemoryEventStore
from swayam.storage.eventstore.port import AppendResult, Cursor, EventStore, StreamPage

__all__ = [
    "AppendResult",
    "Cursor",
    "EventStore",
    "InMemoryEventStore",
    "StreamPage",
]
