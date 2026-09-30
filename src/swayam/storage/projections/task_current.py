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

    def apply(self, recorded: RecordedEvent) -> None:
        if recorded.stream_type != "task":
            return
        # Idempotent per event. Incremental application re-reads streams after each
        # turn; without this guard a re-applied task.note_added would duplicate the
        # note, and the projection would silently drift from the log.
        if recorded.seq <= self._last_seq.get(recorded.stream_id, 0):
            return
        task = self._tasks.get(recorded.stream_id)
        if task is None:
            task = Task(task_id=recorded.stream_id)
            self._tasks[recorded.stream_id] = task
        task.apply(recorded)
        self._last_seq[recorded.stream_id] = recorded.seq

    def rebuild(self, events: Iterable[RecordedEvent]) -> None:
        self._tasks.clear()
        self._last_seq.clear()
        self._cursor = None
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
