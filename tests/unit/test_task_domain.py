"""Task aggregate: the invariant table from docs/phase1_arch.md section 4.3.

Pure, no I/O, no clock -- which is the point. If these needed a database, the domain
would be in the wrong layer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from swayam.core.errors import DomainError, InvariantViolation, ValidationError
from swayam.domain.task import (
    ALLOWED_TRANSITIONS,
    AddNote,
    ArchiveTask,
    ChangeEstimate,
    ChangePriority,
    ChangeStatus,
    Command,
    CommandContext,
    CreateTask,
    LinkToEvent,
    RenameTask,
    ScheduleTask,
    Task,
    TaskStatus,
)
from swayam.events.envelope import Actor, Event, RecordedEvent

PROJECT = uuid4()
TASK_ID = uuid4()
TODAY = date(2026, 9, 29)  # a Tuesday
NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)


def ctx(**overrides: object) -> CommandContext:
    base: dict[str, object] = {
        "now": NOW,
        "actor": Actor.user("owner"),
        "correlation_id": uuid4(),
    }
    base.update(overrides)
    return CommandContext(**base)  # type: ignore[arg-type]


def created_task(title: str = "Send the Q3 report") -> Task:
    task = Task(task_id=TASK_ID)
    for event in task.decide(CreateTask(title=title, project_id=PROJECT), ctx()):
        task.apply(_recorded(task, event, 1))
    return task


def _recorded(task: Task, event: Event, seq: int) -> RecordedEvent:
    return RecordedEvent(
        event=event,
        stream_id=task.task_id,
        stream_type="task",
        seq=seq,
        recorded_at=NOW,
        version=1,
    )


def apply(task: Task, command: Command, context: CommandContext | None = None) -> list:
    events = task.decide(command, context or ctx())
    seq = task.version
    for event in events:
        seq += 1
        task.apply(_recorded(task, event, seq))
    return events


class TestCreation:
    def test_create_produces_one_event(self) -> None:
        task = Task(task_id=TASK_ID)
        events = task.decide(CreateTask(title="Write spec", project_id=PROJECT), ctx())
        assert len(events) == 1
        assert events[0].type == "task.created"
        assert events[0].payload["title"] == "Write spec"

    def test_new_task_starts_open_and_undated(self) -> None:
        task = created_task()
        assert task.exists
        assert task.status is TaskStatus.OPEN
        assert task.due_date is None
        assert task.is_open

    def test_title_is_trimmed(self) -> None:
        task = created_task("  spaced out  ")
        assert task.title == "spaced out"

    def test_blank_title_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Task(task_id=TASK_ID).decide(CreateTask(title="   ", project_id=PROJECT), ctx())

    def test_commands_other_than_create_are_rejected_before_existence(self) -> None:
        with pytest.raises(DomainError, match="does not exist"):
            Task(task_id=TASK_ID).decide(RenameTask(new_title="x"), ctx())

    def test_creating_twice_is_an_invariant_violation(self) -> None:
        with pytest.raises(InvariantViolation, match="already exists"):
            created_task().decide(CreateTask(title="again", project_id=PROJECT), ctx())


class TestTransitions:
    @pytest.mark.parametrize(
        ("start", "target"),
        sorted(((a, b) for a, b in ALLOWED_TRANSITIONS), key=lambda p: (p[0].value, p[1].value)),
    )
    def test_every_declared_transition_is_accepted(
        self, start: TaskStatus, target: TaskStatus
    ) -> None:
        task = created_task()
        task.status = start
        # blocked_by is only meaningful when the target is BLOCKED; the domain rejects
        # it otherwise, so it is supplied conditionally rather than universally.
        blocked_by = "waiting on finance" if target is TaskStatus.BLOCKED else None
        events = apply(task, ChangeStatus(to_status=target, blocked_by=blocked_by))
        assert events[0].type == "task.status_changed"

    @pytest.mark.parametrize(
        ("start", "target"),
        [
            (a, b)
            for a in TaskStatus
            for b in TaskStatus
            if a is not b and (a, b) not in ALLOWED_TRANSITIONS
        ],
    )
    def test_every_undeclared_transition_is_refused(
        self, start: TaskStatus, target: TaskStatus
    ) -> None:
        """I4: illegal transitions raise rather than silently no-op."""
        task = created_task()
        task.status = start
        with pytest.raises(InvariantViolation, match="illegal transition"):
            apply(task, ChangeStatus(to_status=target))

    def test_transition_to_the_current_status_is_a_domain_error(self) -> None:
        with pytest.raises(DomainError, match="already open"):
            created_task().decide(ChangeStatus(to_status=TaskStatus.OPEN), ctx())

    def test_blocking_requires_a_reason(self) -> None:
        with pytest.raises(ValidationError, match="blocked_by is required"):
            created_task().decide(ChangeStatus(to_status=TaskStatus.BLOCKED), ctx())

    def test_blocked_by_is_rejected_when_not_blocking(self) -> None:
        with pytest.raises(ValidationError, match="only meaningful"):
            created_task().decide(
                ChangeStatus(to_status=TaskStatus.COMPLETE, blocked_by="x"), ctx()
            )

    def test_a_completed_task_is_not_open(self) -> None:
        task = created_task()
        apply(task, ChangeStatus(to_status=TaskStatus.COMPLETE))
        assert not task.is_open


class TestScheduling:
    def test_first_due_date_emits_set(self) -> None:
        events = apply(created_task(), ScheduleTask(due_date=date(2026, 10, 2)))
        assert events[0].type == "task.due_date_set"

    def test_second_due_date_emits_move_not_overwrite(self) -> None:
        """I2: a task has at most one due date, so reassignment is a move."""
        task = created_task()
        apply(task, ScheduleTask(due_date=date(2026, 10, 2)))
        events = apply(task, ScheduleTask(due_date=date(2026, 10, 9), reason="slipped"))
        assert events[0].type == "task.due_date_moved"
        assert events[0].payload["from_date"] == "2026-10-02"
        assert events[0].payload["to_date"] == "2026-10-09"
        assert task.due_date == date(2026, 10, 9)

    def test_rescheduling_to_the_same_date_is_a_no_op(self) -> None:
        task = created_task()
        apply(task, ScheduleTask(due_date=date(2026, 10, 2)))
        assert apply(task, ScheduleTask(due_date=date(2026, 10, 2))) == ()

    def test_backdating_is_refused_by_default(self) -> None:
        """I3: backdating is possible, but must be asked for explicitly."""
        with pytest.raises(ValidationError, match="in the past"):
            created_task().decide(ScheduleTask(due_date=date(2026, 9, 1)), ctx())

    def test_backdating_is_permitted_when_explicit(self) -> None:
        task = created_task()
        apply(task, ScheduleTask(due_date=date(2026, 9, 1), allow_backdate=True))
        assert task.due_date == date(2026, 9, 1)


class TestOtherCommands:
    def test_renaming_to_the_same_title_is_a_no_op(self) -> None:
        task = created_task("Send report")
        assert apply(task, RenameTask(new_title="Send report")) == ()
        assert apply(task, RenameTask(new_title="Send the report")) != ()

    def test_priority_is_bounded(self) -> None:
        with pytest.raises(ValidationError, match="priority must be 0-4"):
            created_task().decide(ChangePriority(to_priority=9), ctx())

    def test_estimate_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="must be positive"):
            created_task().decide(ChangeEstimate(to_minutes=0), ctx())

    def test_notes_accumulate_in_order(self) -> None:
        task = created_task()
        apply(task, AddNote(body="client wants a data pass"))
        apply(task, AddNote(body="waiting on finance"))
        assert task.notes == ("client wants a data pass", "waiting on finance")

    def test_blank_note_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be blank"):
            created_task().decide(AddNote(body="  "), ctx())

    def test_linking_the_same_event_twice_is_refused(self) -> None:
        task = created_task()
        event_id = uuid4()
        apply(task, LinkToEvent(event_id=event_id))
        with pytest.raises(DomainError, match="already linked"):
            apply(task, LinkToEvent(event_id=event_id))

    def test_archived_tasks_reject_further_mutation(self) -> None:
        task = created_task()
        apply(task, ArchiveTask(reason="superseded"))
        assert task.archived
        with pytest.raises(InvariantViolation, match="archived"):
            apply(task, RenameTask(new_title="anything"))


class TestReducer:
    def test_replaying_a_stream_reproduces_state(self) -> None:
        live = created_task()
        apply(live, ScheduleTask(due_date=date(2026, 10, 2)))
        apply(live, AddNote(body="client wants a data pass"))
        apply(live, ChangeStatus(to_status=TaskStatus.COMPLETE))

        replayed = Task(task_id=TASK_ID)
        stream = [
            *_create_events(),
            *_schedule_events(),
            *_note_events(),
            *_complete_events(),
        ]
        for seq, event in enumerate(stream, start=1):
            replayed.apply(_recorded(replayed, event, seq))

        assert replayed.title == live.title
        assert replayed.due_date == live.due_date
        assert replayed.notes == live.notes
        assert replayed.status is live.status
        assert replayed.version == live.version == 4

    def test_version_tracks_the_event_sequence(self) -> None:
        task = created_task()
        assert task.version == 1
        apply(task, AddNote(body="one"))
        assert task.version == 2

    def test_applying_an_unknown_event_is_an_invariant_violation(self) -> None:
        task = created_task()
        foreign = RecordedEvent(
            event=Event(
                type="reminder.fired",
                payload={},
                occurred_at=NOW,
                actor=Actor.system(),
                correlation_id=uuid4(),
            ),
            stream_id=task.task_id,
            stream_type="task",
            seq=2,
            recorded_at=NOW,
            version=1,
        )
        with pytest.raises(InvariantViolation, match="cannot apply event"):
            task.apply(foreign)


def _create_events() -> list:
    return list(Task(task_id=TASK_ID).decide(CreateTask(title="Send the Q3 report",
                                                       project_id=PROJECT), ctx()))


def _schedule_events() -> list:
    task = created_task()
    return list(task.decide(ScheduleTask(due_date=date(2026, 10, 2)), ctx()))


def _note_events() -> list:
    task = created_task()
    apply(task, ScheduleTask(due_date=date(2026, 10, 2)))
    return list(task.decide(AddNote(body="client wants a data pass"), ctx()))


def _complete_events() -> list:
    task = created_task()
    apply(task, ScheduleTask(due_date=date(2026, 10, 2)))
    apply(task, AddNote(body="client wants a data pass"))
    return list(task.decide(ChangeStatus(to_status=TaskStatus.COMPLETE), ctx()))
