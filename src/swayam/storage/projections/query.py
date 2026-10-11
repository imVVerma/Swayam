"""Query projections for the fast path.

These projections answer common questions from the event log without LLM involvement.
All queries are deterministic and based on the current projected state.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import NamedTuple, TypedDict
from uuid import UUID

from swayam.domain.task import Task
from swayam.events.catalogue import CALENDAR_EVENT_CREATED, TASK_UNDONE
from swayam.events.envelope import RecordedEvent
from swayam.storage.eventstore.port import Cursor


class EventPayload(TypedDict):
    """Typed dictionary for event payload."""
    event_id: UUID
    title: str
    kind: str
    starts_at: datetime
    ends_at: datetime
    location: str | None
    notes: str | None
    all_day: bool


class TaskView(NamedTuple):
    """Read-only view of a task for query results."""
    task_id: UUID
    title: str
    due_date: date | None
    due_time: time | None
    status: str
    is_overdue: bool


class EventView(NamedTuple):
    """Read-only view of an event for query results."""
    event_id: UUID
    title: str
    kind: str
    starts_at: datetime
    ends_at: datetime
    location: str | None
    notes: str | None
    all_day: bool


class ClashWarning(NamedTuple):
    """A pair of overlapping events."""
    event1: EventView
    event2: EventView


class QueryProjection:
    """Query projections for tasks and events.
    
    All queries are answered from the current projected state, no LLM involved.
    """

    def __init__(self) -> None:
        self._tasks: dict[UUID, Task] = {}
        self._events: dict[UUID, EventPayload] = {}
        self._last_seq: dict[UUID, int] = {}
        self._cursor: Cursor | None = None
        self._undone_correlation_ids: set[UUID] = set()

    def apply(self, recorded: RecordedEvent) -> None:
        """Apply an event to the projection."""
        if recorded.stream_type == "task":
            self._apply_task(recorded)
        elif recorded.stream_type == "event":
            self._apply_event(recorded)

    def _apply_task(self, recorded: RecordedEvent) -> None:
        if recorded.seq <= self._last_seq.get(recorded.stream_id, 0):
            return

        if recorded.event.type == TASK_UNDONE.name:
            self._apply_undo(recorded)
            self._last_seq[recorded.stream_id] = recorded.seq
            return

        if recorded.event.correlation_id in self._undone_correlation_ids:
            return

        task = self._tasks.get(recorded.stream_id)
        if task is None:
            task = Task(task_id=recorded.stream_id)
            self._tasks[recorded.stream_id] = task
        task.apply(recorded)
        self._last_seq[recorded.stream_id] = recorded.seq

    def _apply_event(self, recorded: RecordedEvent) -> None:
        if recorded.seq <= self._last_seq.get(recorded.stream_id, 0):
            return

        if recorded.event.type == CALENDAR_EVENT_CREATED.name:
            payload = recorded.event.payload
            self._events[recorded.stream_id] = {
                "event_id": recorded.stream_id,
                "title": payload.get("title", ""),
                "kind": payload.get("kind", "event"),
                "starts_at": datetime.fromisoformat(payload["starts_at"]),
                "ends_at": datetime.fromisoformat(payload["ends_at"]),
                "location": payload.get("location"),
                "notes": payload.get("notes"),
                "all_day": payload.get("all_day", False),
            }
        self._last_seq[recorded.stream_id] = recorded.seq

    def _apply_undo(self, recorded: RecordedEvent) -> None:
        payload = recorded.event.payload
        undone_correlation_id = UUID(payload["undone_correlation_id"])
        self._undone_correlation_ids.add(undone_correlation_id)

    def rebuild(self, events: Iterable[RecordedEvent]) -> None:
        self._tasks.clear()
        self._events.clear()
        self._last_seq.clear()
        self._cursor = None

        undone_correlation_ids: set[UUID] = set()
        for recorded in events:
            if recorded.stream_type == "task" and recorded.event.type == TASK_UNDONE.name:
                payload = recorded.event.payload
                undone_correlation_ids.add(UUID(payload["undone_correlation_id"]))

        self._undone_correlation_ids.update(undone_correlation_ids)

        for recorded in events:
            self.apply(recorded)

    def _is_overdue(self, task: Task, today: date) -> bool:
        if task.due_date is None:
            return False
        if task.due_date < today:
            return True
        if task.due_date == today and task.due_time:
            now_time = datetime.now().time()
            return task.due_time < now_time
        return False

    # --- Query methods ---

    def tasks_today(self, today: date) -> list[TaskView]:
        """Tasks due today (including overdue)."""
        results = []
        for task in self._tasks.values():
            if not task.exists or task.archived:
                continue
            if task.due_date is None:
                continue
            if task.due_date <= today:
                results.append(TaskView(
                    task_id=task.task_id,
                    title=task.title or "",
                    due_date=task.due_date,
                    due_time=task.due_time,
                    status=task.status.value,
                    is_overdue=self._is_overdue(task, today),
                ))
        results.sort(key=lambda v: (v.due_time or time.min, v.title))
        return results

    def tasks_this_week(self, today: date) -> list[TaskView]:
        """Tasks due this week (Monday to Sunday)."""
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        results = []
        for task in self._tasks.values():
            if not task.exists or task.archived:
                continue
            if task.due_date is None:
                continue
            if start_of_week <= task.due_date <= end_of_week:
                results.append(TaskView(
                    task_id=task.task_id,
                    title=task.title or "",
                    due_date=task.due_date,
                    due_time=task.due_time,
                    status=task.status.value,
                    is_overdue=self._is_overdue(task, today),
                ))
        results.sort(key=lambda v: (v.due_date, v.due_time or time.min, v.title))
        return results

    def tasks_overdue(self, today: date) -> list[TaskView]:
        """Tasks that are past their due date/time."""
        results = []
        for task in self._tasks.values():
            if not task.exists or task.archived:
                continue
            if self._is_overdue(task, today):
                results.append(TaskView(
                    task_id=task.task_id,
                    title=task.title or "",
                    due_date=task.due_date,
                    due_time=task.due_time,
                    status=task.status.value,
                    is_overdue=True,
                ))
        results.sort(key=lambda v: (v.due_date or date.min, v.due_time or time.min, v.title))
        return results

    def tasks_upcoming(self, today: date, days: int = 30) -> list[TaskView]:
        """Tasks due in the next N days."""
        end_date = today + timedelta(days=days)
        results = []
        for task in self._tasks.values():
            if not task.exists or task.archived:
                continue
            if task.due_date is None:
                continue
            if today < task.due_date <= end_date:
                results.append(TaskView(
                    task_id=task.task_id,
                    title=task.title or "",
                    due_date=task.due_date,
                    due_time=task.due_time,
                    status=task.status.value,
                    is_overdue=self._is_overdue(task, today),
                ))
        results.sort(key=lambda v: (v.due_date, v.due_time or time.min, v.title))
        return results

    def tasks_completed(self, since_days: int = 30) -> list[TaskView]:
        """Tasks completed in the last N days."""
        from swayam.core.clock import IST
        cutoff = datetime.now(IST) - timedelta(days=since_days)
        results = []
        for task in self._tasks.values():
            if not task.exists:
                continue
            if task.status.value == "complete" and task.updated_at and task.updated_at >= cutoff:
                results.append(TaskView(
                    task_id=task.task_id,
                    title=task.title or "",
                    due_date=task.due_date,
                    due_time=task.due_time,
                    status=task.status.value,
                    is_overdue=False,
                ))
        results.sort(key=lambda v: v.task_id)
        return results

    def events_today(self, today: date) -> list[EventView]:
        """Events happening today."""
        start_of_day = datetime.combine(today, time.min)
        end_of_day = datetime.combine(today, time.max)
        results = []
        for event in self._events.values():
            if event["starts_at"] <= end_of_day and event["ends_at"] >= start_of_day:
                results.append(EventView(
                    event_id=event["event_id"],
                    title=event["title"],
                    kind=event["kind"],
                    starts_at=event["starts_at"],
                    ends_at=event["ends_at"],
                    location=event["location"],
                    notes=event["notes"],
                    all_day=event["all_day"],
                ))
        results.sort(key=lambda v: v.starts_at)
        return results

    def events_this_week(self, today: date) -> list[EventView]:
        """Events happening this week."""
        start_of_week = datetime.combine(today - timedelta(days=today.weekday()), time.min)
        end_of_week = datetime.combine(start_of_week.date() + timedelta(days=6), time.max)
        results = []
        for event in self._events.values():
            if event["starts_at"] <= end_of_week and event["ends_at"] >= start_of_week:
                results.append(EventView(
                    event_id=event["event_id"],
                    title=event["title"],
                    kind=event["kind"],
                    starts_at=event["starts_at"],
                    ends_at=event["ends_at"],
                    location=event["location"],
                    notes=event["notes"],
                    all_day=event["all_day"],
                ))
        results.sort(key=lambda v: v.starts_at)
        return results

    def events_upcoming(self, today: date, days: int = 30) -> list[EventView]:
        """Events happening in the next N days."""
        start = datetime.combine(today, time.min)
        end = datetime.combine(today + timedelta(days=days), time.max)
        results = []
        for event in self._events.values():
            if event["starts_at"] <= end and event["ends_at"] >= start:
                results.append(EventView(
                    event_id=event["event_id"],
                    title=event["title"],
                    kind=event["kind"],
                    starts_at=event["starts_at"],
                    ends_at=event["ends_at"],
                    location=event["location"],
                    notes=event["notes"],
                    all_day=event["all_day"],
                ))
        results.sort(key=lambda v: v.starts_at)
        return results

    def detect_clashes(self, today: date) -> list[ClashWarning]:
        """Detect overlapping events (basic clash warning)."""
        events = self.events_upcoming(today, days=90)  # Check wider range for clashes
        clashes = []
        for i, e1 in enumerate(events):
            for e2 in events[i+1:]:
                if self._overlaps(e1, e2):
                    clashes.append(ClashWarning(e1, e2))
        return clashes

    def _overlaps(self, e1: EventView, e2: EventView) -> bool:
        """Check if two events overlap in time."""
        # Overlap if one starts before the other ends AND the other starts before this ends
        return e1.starts_at < e2.ends_at and e2.starts_at < e1.ends_at

    # --- Projection interface compatibility ---

    def get(self, task_id: UUID) -> Task | None:
        return self._tasks.get(task_id)

    def all_tasks(self) -> list[Task]:
        return sorted(
            self._tasks.values(),
            key=lambda t: (t.due_date is None, t.due_date or date.max, t.title or ""),
        )

    def open_tasks(self) -> list[Task]:
        return [t for t in self.all_tasks() if t.is_open]

    @property
    def cursor(self) -> Cursor | None:
        return self._cursor