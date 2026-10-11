"""Phase 1 event catalogue.

One entry per event name, each bound to a validated payload schema. This file is the
event log design in executable form (docs/phase1_arch.md section 5.2).

Scope note: ``task.*`` and ``project.*`` are backed by aggregates in ``swayam.domain``.
The temporal (``event.*``, ``reminder.*``) and ``stream.voided`` schemas are declared
here because they are part of the log design, but their aggregates land in milestone
M3 -- declaring a payload schema is not building a module.

Health and feedback events are deliberately absent. Phase 2/4 additions go here; the
envelope, the registry and the store contract all tolerate them without migration.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from swayam.events.registry import EventRegistry, EventType

# Free text that will be shown back to the user. Stripped and length-bounded at the
# schema layer so a blank title is rejected on the way into the log, not only by the
# domain -- the log must never be able to hold a payload the domain would refuse.
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Title = ShortText
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


# --------------------------------------------------------------------------- task


class TaskCreated(BaseModel):
    title: Title
    project_id: UUID
    priority: int = Field(default=0, ge=0, le=4)
    estimate_minutes: int | None = Field(default=None, gt=0)


class TaskRenamed(BaseModel):
    old_title: str
    new_title: Title


class TaskStatusChanged(BaseModel):
    from_status: str
    to_status: str
    reason: str | None = None
    blocked_by: str | None = None


class TaskDueDateSet(BaseModel):
    """First due date. Distinguished from a move so 'set' vs 'rescheduled' is legible
    in the timeline rather than being inferred from a prior state."""

    due_date: date
    due_time: time | None = None


class TaskDueDateMoved(BaseModel):
    from_date: date
    to_date: date
    from_time: time | None = None
    to_time: time | None = None
    reason: str | None = None


class TaskPriorityChanged(BaseModel):
    from_priority: int
    to_priority: int


class TaskEstimated(BaseModel):
    from_minutes: int | None = None
    to_minutes: int | None = None


class TaskLinkedToEvent(BaseModel):
    event_id: UUID
    relationship: str = "context"


class TaskNoteAdded(BaseModel):
    body: Note


class TaskArchived(BaseModel):
    reason: str | None = None


class TaskUndone(BaseModel):
    """Compensating event that records an undo of the last operation.

    The orchestrator writes this when the user invokes `undo`. It carries the
    correlation_id of the turn being undone so the projection can reverse its
    effect without mutating past events.
    """

    undone_correlation_id: UUID
    undone_event_types: tuple[str, ...]
    reason: str = "user requested undo"


# ------------------------------------------------------------------------ project


class ProjectCreated(BaseModel):
    name: Name
    description: str | None = None
    is_default: bool = False


class ProjectClosed(BaseModel):
    reason: str | None = None


# ------------------------------------------------------------------------ temporal


class CalendarEventCreated(BaseModel):
    title: Title
    starts_at: datetime
    ends_at: datetime
    kind: str = "event"  # exam, appointment, birthday, booking, conference, etc.
    location: str | None = None
    notes: str | None = None
    # For multi-day events, ends_at date can be different from starts_at date
    all_day: bool = False


class CalendarEventRescheduled(BaseModel):
    from_starts_at: datetime
    to_starts_at: datetime
    to_ends_at: datetime | None = None
    reason: str | None = None


class CalendarEventCancelled(BaseModel):
    reason: str | None = None


class ReminderSet(BaseModel):
    fire_at: datetime
    subject_stream_id: UUID
    subject_event_type: str


class ReminderFired(BaseModel):
    reminder_id: UUID
    fire_at: datetime


class ReminderSnoozed(BaseModel):
    from_fire_at: datetime
    to_fire_at: datetime


# --------------------------------------------------------------------------- meta


class StreamVoided(BaseModel):
    """Erasure marker. The payload is tombstoned, not dropped, until the retention
    window expires -- an erasure request must be auditable as having happened."""

    scope: str = Field(description="'stream' or 'user'")
    tombstone_hash: str = Field(min_length=1)


# ------------------------------------------------------------------ type constants

TASK_CREATED = EventType("task.created", TaskCreated)
TASK_RENAMED = EventType("task.renamed", TaskRenamed)
TASK_STATUS_CHANGED = EventType("task.status_changed", TaskStatusChanged)
TASK_DUE_DATE_SET = EventType("task.due_date_set", TaskDueDateSet)
TASK_DUE_DATE_MOVED = EventType("task.due_date_moved", TaskDueDateMoved)
TASK_PRIORITY_CHANGED = EventType("task.priority_changed", TaskPriorityChanged)
TASK_ESTIMATED = EventType("task.estimated", TaskEstimated)
TASK_LINKED_TO_EVENT = EventType("task.linked_to_event", TaskLinkedToEvent)
TASK_NOTE_ADDED = EventType("task.note_added", TaskNoteAdded)
TASK_ARCHIVED = EventType("task.archived", TaskArchived)
TASK_UNDONE = EventType("task.undone", TaskUndone)

PROJECT_CREATED = EventType("project.created", ProjectCreated)
PROJECT_CLOSED = EventType("project.closed", ProjectClosed)

CALENDAR_EVENT_CREATED = EventType("event.created", CalendarEventCreated)
CALENDAR_EVENT_RESCHEDULED = EventType("event.rescheduled", CalendarEventRescheduled)
CALENDAR_EVENT_CANCELLED = EventType("event.cancelled", CalendarEventCancelled)

REMINDER_SET = EventType("reminder.set", ReminderSet)
REMINDER_FIRED = EventType("reminder.fired", ReminderFired)
REMINDER_SNOOZED = EventType("reminder.snoozed", ReminderSnoozed)

STREAM_VOIDED = EventType("stream.voided", StreamVoided)

TASK_EVENT_TYPES: tuple[EventType, ...] = (
    TASK_CREATED,
    TASK_RENAMED,
    TASK_STATUS_CHANGED,
    TASK_DUE_DATE_SET,
    TASK_DUE_DATE_MOVED,
    TASK_PRIORITY_CHANGED,
    TASK_ESTIMATED,
    TASK_LINKED_TO_EVENT,
    TASK_NOTE_ADDED,
    TASK_ARCHIVED,
    TASK_UNDONE,
)

PHASE1_EVENT_TYPES: tuple[EventType, ...] = (
    *TASK_EVENT_TYPES,
    PROJECT_CREATED,
    PROJECT_CLOSED,
    CALENDAR_EVENT_CREATED,
    CALENDAR_EVENT_RESCHEDULED,
    CALENDAR_EVENT_CANCELLED,
    REMINDER_SET,
    REMINDER_FIRED,
    REMINDER_SNOOZED,
    STREAM_VOIDED,
)

registry = EventRegistry(PHASE1_EVENT_TYPES)
