"""Repositories: the only sanctioned bridge between ``domain/`` and the event log.

A repository does exactly three things -- load an aggregate by replaying its stream,
hand a command to ``decide``, and append the resulting events with the aggregate's
version as the optimistic-concurrency token. It adds no caching and no business rules,
because both would become a second source of truth the event log cannot correct.

These live under ``storage/`` rather than ``domain/`` on purpose. A repository depends on
``EventStore``, and the architectural claim worth keeping true is that ``domain/`` is
importable with no database, no clock and no I/O of any kind. Keeping the bridge on the
storage side is what makes that claim checkable rather than aspirational; the fitness
test in ``tests/arch/`` enforces it.
"""

from __future__ import annotations

from uuid import UUID

from swayam.core.clock import Clock
from swayam.core.ids import CorrelationId, new_id
from swayam.domain.project import PROJECT_STREAM_TYPE, Project
from swayam.domain.task import TASK_STREAM_TYPE, Command, CommandContext, CreateTask, Task
from swayam.events.catalogue import PROJECT_CREATED
from swayam.events.envelope import Actor, Event
from swayam.storage.eventstore.port import EventStore

DEFAULT_PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")
"""Fixed id for the implicit default project. Deterministic so the mock runtime, the
CLI and the test fixtures all agree on where uncategorised tasks land."""


class TaskRepository:
    def __init__(self, store: EventStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def get(self, task_id: UUID) -> Task:
        """Rebuild current state by replaying the stream. No cache: correctness first,
        and the projection layer exists for reads that need to be fast."""
        task = Task(task_id=task_id)
        for recorded in await self._store.read_stream(task_id):
            task.apply(recorded)
        return task

    async def execute(
        self,
        task_id: UUID,
        command: Command,
        *,
        actor: Actor,
        correlation_id: CorrelationId,
        causation_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        task = await self.get(task_id)
        ctx = CommandContext(
            now=self._clock.now(),
            actor=actor,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
        )
        return await self._commit(task, command, ctx)

    async def create(
        self,
        command: CreateTask,
        *,
        task_id: UUID | None = None,
        actor: Actor,
        correlation_id: CorrelationId,
        causation_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        task_id = task_id or new_id()
        ctx = CommandContext(
            now=self._clock.now(),
            actor=actor,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
        )
        return await self._commit(Task(task_id=task_id), command, ctx)

    async def _commit(self, task: Task, command: Command, ctx: CommandContext) -> Task:
        events = task.decide(command, ctx)
        if not events:
            return task
        result = await self._store.append(
            stream_id=task.task_id,
            stream_type=TASK_STREAM_TYPE,
            expected_version=task.version,
            events=events,
            correlation_id=ctx.correlation_id,
            idempotency_key=ctx.idempotency_key,
        )
        for recorded in result.events:
            task.apply(recorded)
        return task


class ProjectRepository:
    def __init__(self, store: EventStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def get(self, project_id: UUID) -> Project:
        project = Project(project_id=project_id)
        for recorded in await self._store.read_stream(project_id):
            project.apply(recorded)
        return project

    async def ensure_default(self, *, actor: Actor, correlation_id: CorrelationId) -> Project:
        """Idempotently create the default project. Safe to call on every boot."""
        existing = await self.get(DEFAULT_PROJECT_ID)
        if existing.exists:
            return existing
        event = Event(
            type=PROJECT_CREATED.name,
            payload={"name": "Inbox", "is_default": True},
            occurred_at=self._clock.now(),
            actor=actor,
            correlation_id=correlation_id,
        )
        result = await self._store.append(
            stream_id=DEFAULT_PROJECT_ID,
            stream_type=PROJECT_STREAM_TYPE,
            expected_version=0,
            events=(event,),
            correlation_id=correlation_id,
            idempotency_key="bootstrap.default-project",
        )
        project = Project(project_id=DEFAULT_PROJECT_ID)
        for recorded in result.events:
            project.apply(recorded)
        return project
