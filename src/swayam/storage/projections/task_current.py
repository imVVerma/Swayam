"""Read-model projections.

A projection is a fold over the global log. It is disposable: delete it, replay the
log, get the same answer. The fitness function that matters is
``tests/unit/test_projection_replay.py`` -- replay from empty and assert convergence --
because a projection that is subtly wrong is worse than one that is slow, since the
fast path trusts it without re-deriving anything.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from uuid import UUID

from swayam.domain.task import Task
from swayam.events.catalogue import TASK_UNDONE
from swayam.events.envelope import RecordedEvent
from swayam.storage.eventstore.port import Cursor


class TaskCurrentProjection:
    """One row per task: the current state, for the fast path.

    ``task_current`` is the only projection Phase 1 strictly needs. The other four in
    docs/phase1_arch.md section 6.2 land with M3.
    """

    def __init__(self) -> None:
        self._tasks: dict[UUID, Task] = {}
        self._last_seq: dict[UUID, int] = {}
        self._cursor: Cursor | None = None
        self._undone_correlation_ids: set[UUID] = set()

    def apply(self, recorded: RecordedEvent) -> None:
        if recorded.stream_type != "task":
            return
        # Idempotent per event. Incremental application re-reads streams after each
        # turn; without this guard a re-applied task.note_added would duplicate the
        # note, and the projection would silently drift from the log.
        if recorded.seq <= self._last_seq.get(recorded.stream_id, 0):
            return

        if recorded.event.type == TASK_UNDONE.name:
            self._apply_undo(recorded)
            self._last_seq[recorded.stream_id] = recorded.seq
            return

        # Skip events that were undone by a compensating event.
        if recorded.event.correlation_id in self._undone_correlation_ids:
            return

        task = self._tasks.get(recorded.stream_id)
        if task is None:
            task = Task(task_id=recorded.stream_id)
            self._tasks[recorded.stream_id] = task
        task.apply(recorded)
        self._last_seq[recorded.stream_id] = recorded.seq

    def _apply_undo(self, recorded: RecordedEvent) -> None:
        """Mark the correlation_id as undone. Rebuild is handled by the caller."""
        payload = recorded.event.payload
        undone_correlation_id = UUID(payload["undone_correlation_id"])
        self._undone_correlation_ids.add(undone_correlation_id)

    def rebuild(self, events: Iterable[RecordedEvent]) -> None:
        self._tasks.clear()
        self._last_seq.clear()
        self._cursor = None

        # First pass: collect all undone correlation_ids from task.undone events.
        # This must happen before applying other events, because the undo event
        # is typically the last event in the log, but its effect applies to
        # earlier events from the same correlation_id.
        undone_correlation_ids: set[UUID] = set()
        for recorded in events:
            if recorded.stream_type == "task" and recorded.event.type == TASK_UNDONE.name:
                payload = recorded.event.payload
                undone_correlation_ids.add(UUID(payload["undone_correlation_id"]))

        # Merge with any previously known undone correlation_ids (e.g., from
        # an undo event applied in the current session before rebuild).
        self._undone_correlation_ids.update(undone_correlation_ids)

        # Second pass: apply all events, skipping those from undone correlation_ids.
        for recorded in events:
            self.apply(recorded)

    @property
    def cursor(self) -> Cursor | None:
        return self._cursor

    def get(self, task_id: UUID) -> Task | None:
        return self._tasks.get(task_id)

    def all_tasks(self) -> list[Task]:
        """Sorted by due date, undated last, then by title for stability."""
        return sorted(
            self._tasks.values(),
            key=lambda t: (t.due_date is None, t.due_date or date.max, t.title or ""),
        )

    def open_tasks(self) -> list[Task]:
        return [t for t in self.all_tasks() if t.is_open]
