"""Task aggregate.

Pure: no I/O, no clock, no LLM. Commands in, events out, and a reducer that can rebuild
state from a stream. This is the property that lets the mutation pipeline be unit-tested
and lets any projection be validated by replaying the log from empty.

Invariants I1-I7 are documented in docs/phase1_arch.md section 4.3; each is marked
inline below at the point it is enforced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from swayam.core.errors import DomainError, InvariantViolation, ValidationError
from swayam.core.ids import CorrelationId
from swayam.events import catalogue as cat
from swayam.events.envelope import Actor, Event, RecordedEvent

TASK_STREAM_TYPE = "task"


class TaskStatus(StrEnum):
    OPEN = "open"
    BLOCKED = "blocked"
    COMPLETE = "complete"


# COMPLETE -> COMPLETE is absent: re-completing is a no-op, not a transition. COMPLETE ->
# BLOCKED is absent: you cannot block a finished task, and silently allowing it hides
# a real inconsistency in the user's mental model.
ALLOWED_TRANSITIONS: frozenset[tuple[TaskStatus, TaskStatus]] = frozenset(
    {
        (TaskStatus.OPEN, TaskStatus.BLOCKED),
        (TaskStatus.BLOCKED, TaskStatus.OPEN),
        (TaskStatus.OPEN, TaskStatus.COMPLETE),
        (TaskStatus.BLOCKED, TaskStatus.COMPLETE),
        (TaskStatus.COMPLETE, TaskStatus.OPEN),
    }
)


# ---------------------------------------------------------------------- commands


@dataclass(frozen=True, slots=True)
class CreateTask:
    title: str
    project_id: UUID
    priority: int = 0
    estimate_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class RenameTask:
    new_title: str


@dataclass(frozen=True, slots=True)
class ChangeStatus:
    to_status: TaskStatus
    reason: str | None = None
    blocked_by: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduleTask:
    """Set or move the due date.

    One command, two events. Which one is emitted depends on current state, not on
    caller intent -- this is invariant I2: a task has at most one due date, so a second
    assignment is a move, never an overwrite.
    """

    due_date: date
    allow_backdate: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ChangePriority:
    to_priority: int


@dataclass(frozen=True, slots=True)
class ChangeEstimate:
    to_minutes: int | None


@dataclass(frozen=True, slots=True)
class AddNote:
    body: str


@dataclass(frozen=True, slots=True)
class LinkToEvent:
    event_id: UUID


@dataclass(frozen=True, slots=True)
class ArchiveTask:
    reason: str | None = None


Command = (
    CreateTask
    | RenameTask
    | ChangeStatus
    | ScheduleTask
    | ChangePriority
    | ChangeEstimate
    | AddNote
    | LinkToEvent
    | ArchiveTask
)


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Everything a command needs that is not the command.

    Passed in rather than imported so the domain stays deterministic and so the
    conversation turn id can be threaded through as ``causation_id`` (invariant I7).
    """

    now: datetime
    actor: Actor
    correlation_id: CorrelationId
    causation_id: UUID | None = None
    idempotency_key: str | None = None


# ---------------------------------------------------------------------- aggregate


@dataclass(slots=True)
class Task:
    """Current state of a task. Always derivable by replaying its stream."""

    task_id: UUID
    project_id: UUID | None = None
    title: str | None = None
    """``None`` means the task does not exist yet -- the shape a stream replays to
    before its ``task.created`` event."""
    status: TaskStatus = TaskStatus.OPEN
    priority: int = 0
    estimate_minutes: int | None = None
    due_date: date | None = None
    blocked_by: str | None = None
    notes: tuple[str, ...] = ()
    linked_event_ids: tuple[UUID, ...] = ()
    archived: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    version: int = 0

    @property
    def exists(self) -> bool:
        return self.title is not None

    @property
    def is_open(self) -> bool:
        return self.exists and not self.archived and self.status is not TaskStatus.COMPLETE

    # ------------------------------------------------------------------ decisions

    def decide(self, command: Command, ctx: CommandContext) -> tuple[Event, ...]:
        """Validate a command and return the events it would append.

        Returns an empty tuple for a legitimate no-op (renaming to the same title).
        Raises rather than returning an error object: an invalid command is a bug in
        the planner or a misparse, and both should stop the turn, not continue it.
        """
        if not self.exists:
            if not isinstance(command, CreateTask):
                raise DomainError("task does not exist; only CreateTask is valid")
        elif isinstance(command, CreateTask):
            raise InvariantViolation("task already exists")

        match command:
            case CreateTask():
                return (self._decide_create(command, ctx),)
            case RenameTask():
                return self._decide_rename(command, ctx)
            case ChangeStatus():
                return (self._decide_change_status(command, ctx),)
            case ScheduleTask():
                return self._decide_schedule(command, ctx)
            case ChangePriority():
                return (self._decide_priority(command, ctx),)
            case ChangeEstimate():
                return (self._decide_estimate(command, ctx),)
            case AddNote():
                return (self._decide_note(command, ctx),)
            case LinkToEvent():
                return (self._decide_link(command, ctx),)
            case ArchiveTask():
                return (self._decide_archive(command, ctx),)
            case _:
                raise AssertionError(f"no decision rule for {type(command).__name__}")

    def _require_live(self) -> None:
        if self.archived:
            raise InvariantViolation("task is archived; unarchive before mutating")

    def _decide_create(self, cmd: CreateTask, ctx: CommandContext) -> Event:
        title = cmd.title.strip()
        if not title:
            raise ValidationError("title must not be blank")
        if cmd.priority < 0 or cmd.priority > 4:
            raise ValidationError("priority must be 0-4")
        return _event(
            cat.TASK_CREATED.name,
            cat.TaskCreated(
                title=title,
                project_id=cmd.project_id,
                priority=cmd.priority,
                estimate_minutes=cmd.estimate_minutes,
            ).model_dump(mode="json", exclude_none=True),
            ctx,
        )

    def _decide_rename(self, cmd: RenameTask, ctx: CommandContext) -> tuple[Event, ...]:
        self._require_live()
        new_title = cmd.new_title.strip()
        if not new_title:
            raise ValidationError("title must not be blank")
        if new_title == self.title:
            return ()
        return (
            _event(
                cat.TASK_RENAMED.name,
                cat.TaskRenamed(old_title=self.title or "", new_title=new_title).model_dump(
                    mode="json", exclude_none=True
                ),
                ctx,
            ),
        )

    def _decide_change_status(self, cmd: ChangeStatus, ctx: CommandContext) -> Event:
        self._require_live()
        if cmd.to_status is self.status:
            raise DomainError(f"task is already {cmd.to_status.value}")
        # I4: illegal transitions raise rather than silently no-op.
        if (self.status, cmd.to_status) not in ALLOWED_TRANSITIONS:
            raise InvariantViolation(
                f"illegal transition {self.status.value} -> {cmd.to_status.value}"
            )
        if cmd.to_status is TaskStatus.BLOCKED and not cmd.blocked_by:
            raise ValidationError("blocked_by is required when blocking a task")
        if cmd.to_status is not TaskStatus.BLOCKED and cmd.blocked_by:
            raise ValidationError("blocked_by is only meaningful on a blocked task")
        return _event(
            cat.TASK_STATUS_CHANGED.name,
            cat.TaskStatusChanged(
                from_status=self.status.value,
                to_status=cmd.to_status.value,
                reason=cmd.reason,
                blocked_by=cmd.blocked_by,
            ).model_dump(mode="json", exclude_none=True),
            ctx,
        )

    def _decide_schedule(self, cmd: ScheduleTask, ctx: CommandContext) -> tuple[Event, ...]:
        self._require_live()
        # I3: backdating is possible but must be explicit.
        if cmd.due_date < ctx.now.date() and not cmd.allow_backdate:
            raise ValidationError(
                f"due date {cmd.due_date.isoformat()} is in the past; "
                "set allow_backdate=True to schedule it anyway"
            )
        if self.due_date is None:  # I2
            return (
                _event(
                    cat.TASK_DUE_DATE_SET.name,
                    cat.TaskDueDateSet(due_date=cmd.due_date).model_dump(mode="json"),
                    ctx,
                ),
            )
        if self.due_date == cmd.due_date:
            return ()
        return (
            _event(
                cat.TASK_DUE_DATE_MOVED.name,
                cat.TaskDueDateMoved(
                    from_date=self.due_date, to_date=cmd.due_date, reason=cmd.reason
                ).model_dump(mode="json", exclude_none=True),
                ctx,
            ),
        )

    def _decide_priority(self, cmd: ChangePriority, ctx: CommandContext) -> Event:
        self._require_live()
        if not 0 <= cmd.to_priority <= 4:
            raise ValidationError("priority must be 0-4")
        if cmd.to_priority == self.priority:
            raise DomainError("task already has that priority")
        return _event(
            cat.TASK_PRIORITY_CHANGED.name,
            cat.TaskPriorityChanged(
                from_priority=self.priority, to_priority=cmd.to_priority
            ).model_dump(mode="json"),
            ctx,
        )

    def _decide_estimate(self, cmd: ChangeEstimate, ctx: CommandContext) -> Event:
        self._require_live()
        if cmd.to_minutes is not None and cmd.to_minutes <= 0:
            raise ValidationError("estimate must be positive")
        if cmd.to_minutes == self.estimate_minutes:
            raise DomainError("task already has that estimate")
        return _event(
            cat.TASK_ESTIMATED.name,
            cat.TaskEstimated(
                from_minutes=self.estimate_minutes, to_minutes=cmd.to_minutes
            ).model_dump(mode="json", exclude_none=True),
            ctx,
        )

    def _decide_note(self, cmd: AddNote, ctx: CommandContext) -> Event:
        self._require_live()
        body = cmd.body.strip()
        if not body:
            raise ValidationError("note must not be blank")
        return _event(
            cat.TASK_NOTE_ADDED.name,
            cat.TaskNoteAdded(body=body).model_dump(mode="json"),
            ctx,
        )

    def _decide_link(self, cmd: LinkToEvent, ctx: CommandContext) -> Event:
        self._require_live()
        if cmd.event_id in self.linked_event_ids:
            raise DomainError("task is already linked to that event")
        return _event(
            cat.TASK_LINKED_TO_EVENT.name,
            cat.TaskLinkedToEvent(event_id=cmd.event_id).model_dump(mode="json"),
            ctx,
        )

    def _decide_archive(self, cmd: ArchiveTask, ctx: CommandContext) -> Event:
        self._require_live()
        return _event(
            cat.TASK_ARCHIVED.name,
            cat.TaskArchived(reason=cmd.reason).model_dump(mode="json", exclude_none=True),
            ctx,
        )

    # -------------------------------------------------------------------- reducer

    def apply(self, recorded: RecordedEvent) -> None:
        """Fold one recorded event into state. Must be total: never raise on a valid
        event, including ones written by older versions of the schema."""
        payload = recorded.event.payload
        name = recorded.event.type
        occurred = recorded.event.occurred_at

        if name == cat.TASK_CREATED.name:
            self.title = payload["title"]
            self.project_id = UUID(payload["project_id"])
            self.priority = payload.get("priority", 0)
            self.estimate_minutes = payload.get("estimate_minutes")
            self.created_at = occurred
        elif name == cat.TASK_RENAMED.name:
            self.title = payload["new_title"]
        elif name == cat.TASK_STATUS_CHANGED.name:
            self.status = TaskStatus(payload["to_status"])
            self.blocked_by = payload.get("blocked_by")
        elif name == cat.TASK_DUE_DATE_SET.name:
            self.due_date = date.fromisoformat(payload["due_date"])
        elif name == cat.TASK_DUE_DATE_MOVED.name:
            self.due_date = date.fromisoformat(payload["to_date"])
        elif name == cat.TASK_PRIORITY_CHANGED.name:
            self.priority = payload["to_priority"]
        elif name == cat.TASK_ESTIMATED.name:
            self.estimate_minutes = payload.get("to_minutes")
        elif name == cat.TASK_LINKED_TO_EVENT.name:
            self.linked_event_ids = (*self.linked_event_ids, UUID(payload["event_id"]))
        elif name == cat.TASK_NOTE_ADDED.name:
            self.notes = (*self.notes, payload["body"])
        elif name == cat.TASK_ARCHIVED.name:
            self.archived = True
        else:
            raise InvariantViolation(f"task cannot apply event: {name}")

        self.updated_at = occurred
        self.version = recorded.seq


def _event(type_name: str, payload: dict[str, Any], ctx: CommandContext) -> Event:
    return Event(
        type=type_name,
        payload=payload,
        occurred_at=ctx.now,
        actor=ctx.actor,
        correlation_id=ctx.correlation_id,
        causation_id=ctx.causation_id,
        idempotency_key=ctx.idempotency_key,
    )
