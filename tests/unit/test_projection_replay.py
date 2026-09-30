"""Projection convergence.

The fitness function that matters for a projection: replay the entire log from empty
and land on the same state. A projection that is subtly wrong is worse than one that
is slow, because the fast path trusts it without re-deriving anything.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from swayam.core.clock import FrozenClock
from swayam.core.ids import CorrelationId
from swayam.domain.task import AddNote, ChangeStatus, CreateTask, ScheduleTask, TaskStatus
from swayam.events.envelope import Actor
from swayam.storage.bootstrap import build_storage
from swayam.storage.projections.task_current import TaskCurrentProjection
from swayam.storage.repositories import DEFAULT_PROJECT_ID, TaskRepository

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
ACTOR = Actor.user("owner")
CORR = CorrelationId(uuid4())


async def a_log_with_history() -> tuple[object, object]:
    """A log with two tasks: one scheduled and annotated, one completed."""
    storage = build_storage(clock=FrozenClock(NOW))
    repo = TaskRepository(storage.events, storage.clock)

    report = await repo.create(
        CreateTask(title="Send the Q3 report", project_id=DEFAULT_PROJECT_ID),
        actor=ACTOR, correlation_id=CORR,
    )
    await repo.execute(
        report.task_id, ScheduleTask(due_date=date(2026, 10, 2)),
        actor=ACTOR, correlation_id=CORR,
    )
    await repo.execute(
        report.task_id, AddNote(body="client wants a data pass"),
        actor=ACTOR, correlation_id=CORR,
    )

    budget = await repo.create(
        CreateTask(title="Review the budget", project_id=DEFAULT_PROJECT_ID),
        actor=ACTOR, correlation_id=CORR,
    )
    await repo.execute(
        budget.task_id, ChangeStatus(to_status=TaskStatus.COMPLETE),
        actor=ACTOR, correlation_id=CORR,
    )

    return storage.events, await storage.events.read_all()


class TestTaskCurrentProjection:
    async def test_replay_from_empty_converges(self) -> None:
        _, log = await a_log_with_history()

        live = TaskCurrentProjection()
        for recorded in log.events:
            live.apply(recorded)

        replayed = TaskCurrentProjection()
        replayed.rebuild(log.events)

        assert _state(replayed) == _state(live)
        assert len(replayed.all_tasks()) == 2

    async def test_projection_matches_the_aggregate_replayed_independently(self) -> None:
        """Two independent derivations of the same state must agree. The repository
        replays per-aggregate; the projection folds the global log."""
        store, log = await a_log_with_history()
        projection = TaskCurrentProjection()
        projection.rebuild(log.events)
        repo = TaskRepository(store, FrozenClock(NOW))

        for task_id in {e.stream_id for e in log.events}:
            aggregate = await repo.get(task_id)
            projected = projection.get(task_id)
            assert projected is not None
            assert projected.title == aggregate.title
            assert projected.due_date == aggregate.due_date
            assert projected.status is aggregate.status
            assert projected.notes == aggregate.notes
            assert projected.version == aggregate.version

    async def test_applying_the_same_event_twice_changes_nothing(self) -> None:
        """Incremental application re-reads streams after every turn; without the
        per-event guard a re-applied note would duplicate and the projection would
        drift away from the log it claims to represent."""
        _, log = await a_log_with_history()
        projection = TaskCurrentProjection()
        projection.rebuild(log.events)
        before = _state(projection)

        for recorded in log.events:
            projection.apply(recorded)

        assert _state(projection) == before

    async def test_completed_tasks_are_excluded_from_open(self) -> None:
        _, log = await a_log_with_history()
        projection = TaskCurrentProjection()
        projection.rebuild(log.events)
        open_titles = {t.title for t in projection.open_tasks()}
        assert "Send the Q3 report" in open_titles
        assert "Review the budget" not in open_titles

    async def test_undated_tasks_sort_after_dated_ones(self) -> None:
        _, log = await a_log_with_history()
        projection = TaskCurrentProjection()
        projection.rebuild(log.events)
        assert projection.all_tasks()[0].due_date == date(2026, 10, 2)

    async def test_non_task_streams_are_ignored(self) -> None:
        _, log = await a_log_with_history()
        projection = TaskCurrentProjection()
        before = len(projection.all_tasks())
        for recorded in log.events:
            if recorded.stream_type != "task":
                projection.apply(recorded)
        assert len(projection.all_tasks()) == before


def _state(projection: TaskCurrentProjection) -> dict:
    return {
        str(t.task_id): (t.title, t.due_date, t.status, t.notes, t.version, t.archived)
        for t in projection.all_tasks()
    }
