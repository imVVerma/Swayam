"""Turn orchestration.

The wiring diagram in docs/phase1_arch.md section 3, as code. Its job is to hold the
order of operations:

    route -> plan -> validate -> append -> project -> compose

Nothing here makes a decision. Routing is in the router, interpretation in the planner,
validity in the domain, durability in the store, wording in the composer. If this
module starts growing conditionals, a concern has escaped its layer.

Phase 1 implements the mutation path end to end. The query paths return an explicit
"not yet" rather than a guess, because a fast path that answers from projections which
do not exist yet is how a system starts hallucinating.

One invariant is enforced here rather than in either planner, because both planners can
violate it independently: **no part of what the user asked for goes unmentioned.** Every
proposal is attempted, every failure is reported, and any shortfall between the number of
instructions the router found and the number of changes the planner produced is stated
out loud. A reply that quietly reports the easy half of a two-part request is worse than
one that does nothing, because the user believes it all worked.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from uuid import UUID

from swayam.core.clock import Clock
from swayam.core.errors import SwayamError
from swayam.core.ids import CorrelationId
from swayam.domain.task import (
    AddNote,
    ArchiveTask,
    ChangeStatus,
    CreateTask,
    RenameTask,
    ScheduleTask,
    Task,
    TaskStatus,
)
from swayam.events.envelope import Actor, Event, RecordedEvent
from swayam.observability.trace import Trace
from swayam.reasoning.compose.reply import Citation, Reply
from swayam.reasoning.mutation.proposal import ChangeKind, ProposedChange, missing_field_question
from swayam.reasoning.mutation.resolve import describe_candidates, resolve_task
from swayam.reasoning.mutation.rule_planner import MutationPlanner
from swayam.reasoning.router.deterministic import IntentRouter
from swayam.reasoning.router.intent import Intent, IntentDecision
from swayam.storage.eventstore.port import EventStore
from swayam.storage.projections.query import QueryProjection
from swayam.storage.repositories import (
    DEFAULT_PROJECT_ID,
    ProjectRepository,
    TaskRepository,
)

NOT_YET = "That path arrives in M3. Right now I can capture and update tasks."


@dataclass(slots=True)
class TurnResult:
    turn_id: UUID
    intent: Intent
    reply: Reply
    committed: list[RecordedEvent] = field(default_factory=list)
    decision: IntentDecision | None = None
    trace: Trace | None = None


@dataclass(slots=True)
class _Applied:
    events: tuple[RecordedEvent, ...]
    text: str
    citation: Citation | None = None


def _why_no_proposal(decision: IntentDecision, known_tasks: Sequence[Task]) -> str:
    """Ask about the thing that is actually missing.

    "I wasn't sure what to change" paired with "Which task?" is wrong whenever the task
    was identified and something else was missing -- blocking without saying what it is
    blocked on, for instance. Guessing a blocker would write a fabricated dependency into
    the log, so asking is the right move; asking about the wrong thing is not.

    Which field is required for which action comes from ``REQUIRED_FIELDS``, so this cannot
    fall behind the way a hand-written list of five cases did.

    An unresolvable reference is checked first and answered with the candidate list. Asking
    "when should it be due?" about a task that does not exist would presuppose the thing the
    user is about to be told cannot be found.
    """
    instructions = [i for i in decision.slots.get("instructions", ()) or () if isinstance(i, dict)]
    for instruction in instructions:
        target_ref = str(instruction.get("target_ref") or "")
        if target_ref and resolve_task(target_ref, known_tasks) is None:
            # describe_candidates returns a noun phrase ("none of your tasks") meant to be
            # embedded in a longer sentence, so it is wrapped here rather than returned bare.
            candidates = describe_candidates(target_ref, known_tasks)
            return (
                f"I could not tell which task you meant by {target_ref!r}. "
                f"Closest: {candidates}."
            )
    for instruction in instructions:
        action = str(instruction.get("action") or "")
        if not instruction.get("target_ref") and action not in ("create", "create_event"):
            # No reference, so "which task?" is at least the right question.
            continue
        question = missing_field_question(action, instruction)
        if question is not None:
            return question
    if decision.slots.get("title"):
        return "I'm not sure what you'd like to do with that."
    return "Which task, and what should happen to it?"


class TurnOrchestrator:
    def __init__(
        self,
        *,
        store: EventStore,
        clock: Clock,
        router: IntentRouter,
        planner: MutationPlanner,
        projection: QueryProjection,
        user_id: str = "owner",
    ) -> None:
        self._store = store
        self._clock = clock
        self._router = router
        self._planner = planner
        self._projection = projection
        self._user_id = user_id
        self._tasks = TaskRepository(store, clock)
        self._projects = ProjectRepository(store, clock)
        self._last_turn_correlation_id: CorrelationId | None = None
        self._last_turn_event_types: tuple[str, ...] = ()

    async def handle(self, utterance: str) -> TurnResult:
        trace = Trace()
        today = self._clock.now().date()
        correlation_id: CorrelationId = CorrelationId(trace.correlation_id)
        actor = Actor.user(self._user_id)

        decision = await self._router.route(utterance, today=today)
        trace.record(
            "route",
            {
                "intent": decision.intent.value,
                "confidence": round(decision.confidence, 3),
                "rationale": decision.rationale,
            },
        )

        if not decision.is_confident:
            trace.record("clarify", {"reason": "low_confidence"})
            return TurnResult(
                trace.turn_id,
                decision.intent,
                Reply(
                    text="I didn't follow that.",
                    clarification="Could you rephrase? I didn't want to guess at what you meant.",
                ),
                decision=decision,
                trace=trace,
            )

        if decision.intent is Intent.SMALLTALK:
            return TurnResult(
                trace.turn_id,
                decision.intent,
                Reply(text="Ask me about your week."),
                decision=decision,
                trace=trace,
            )

        if decision.intent in (Intent.MUTATION, Intent.CAPTURE):
            result = await self._handle_mutation(utterance, decision, trace, correlation_id, actor)
            if result.committed:
                self._last_turn_correlation_id = correlation_id
                self._last_turn_event_types = tuple(e.event.type for e in result.committed)
            return result

        # STATE / TEMPORAL / LOAD / CROSS_DOMAIN all depend on projections arriving with
        # M3. Refusing is correct here; guessing is not.
        trace.record("deferred", {"intent": decision.intent.value})
        return TurnResult(
            trace.turn_id,
            decision.intent,
            Reply(text=NOT_YET),
            decision=decision,
            trace=trace,
        )

    async def _handle_mutation(
        self,
        utterance: str,
        decision: IntentDecision,
        trace: Trace,
        correlation_id: CorrelationId,
        actor: Actor,
    ) -> TurnResult:
        known_tasks = self._projection.all_tasks()
        proposals = await self._planner.plan(
            utterance,
            decision,
            today=self._clock.now().date(),
            known_tasks=known_tasks,
        )
        trace.record("plan", {"proposals": [p.describe() for p in proposals]})

        if not proposals:
            trace.record("clarify", {"reason": "no_proposal"})
            return TurnResult(
                trace.turn_id,
                decision.intent,
                Reply(
                    text="I wasn't sure what to change.",
                    clarification=_why_no_proposal(decision, known_tasks),
                ),
                decision=decision,
                trace=trace,
            )

        # Check for duplicate task creation proposals.
        for proposal in proposals:
            if proposal.kind is ChangeKind.CREATE_TASK:
                title = str(proposal.fields.get("title", "")).strip().lower()
                if title:
                    existing = [
                        t
                        for t in known_tasks
                        if t.title and t.title.strip().lower() == title and t.exists
                    ]
                    if existing:
                        trace.record("duplicate_detected", {"title": title})
                        return TurnResult(
                            trace.turn_id,
                            decision.intent,
                            Reply(
                                text=f'A task titled "{existing[0].title}" already exists.',
                                clarification="Would you like to update it instead?",
                            ),
                            decision=decision,
                            trace=trace,
                        )

        applied: list[_Applied] = []
        failures: list[str] = []
        for proposal in proposals:
            try:
                item = await self._apply(
                    self._with_live_target(proposal),
                    correlation_id,
                    actor,
                    trace,
                    known_tasks,
                )
            except SwayamError as exc:
                failures.append(str(exc))
                continue
            applied.append(item)
            # Projected as each instruction lands, not in a second pass. Resolution of
            # the next instruction depends on this, and a rejected instruction must not
            # leave anything behind for the next one to see.
            for recorded in item.events:
                self._projection.apply(recorded)

        unhandled = self._unhandled_count(decision, len(proposals))
        if unhandled:
            trace.record(
                "plan_gap",
                {
                    "instructions": self._expected_count(decision),
                    "proposals": len(proposals),
                    "unhandled": unhandled,
                },
            )

        trace.record(
            "commit",
            {"events": sum(len(a.events) for a in applied), "failures": failures},
        )

        gaps = (
            [f"I couldn't make sense of {unhandled} of the "
             f"{unhandled + len(proposals)} things you asked for."]
            if unhandled
            else []
        )
        problems = [*gaps, *failures]

        if not applied:
            return TurnResult(
                trace.turn_id,
                decision.intent,
                Reply(
                    text="I couldn't make that change.",
                    clarification=" ".join(problems) if problems else None,
                ),
                decision=decision,
                trace=trace,
            )

        reply = Reply(
            text=" ".join(a.text for a in applied),
            citations=tuple(a.citation for a in applied if a.citation is not None),
            # Partial success is reported as partial success. Reporting only the part that
            # worked would leave the user believing a rejected instruction had landed.
            clarification=" ".join(problems) if problems else None,
        )
        return TurnResult(
            trace.turn_id,
            decision.intent,
            reply,
            [e for a in applied for e in a.events],
            decision=decision,
            trace=trace,
        )

    @staticmethod
    def _expected_count(decision: IntentDecision) -> int | None:
        expected = decision.slots.get("instruction_count")
        return expected if isinstance(expected, int) else None

    def _with_live_target(self, proposal: ProposedChange) -> ProposedChange:
        """Re-resolve a reference against current state immediately before the write.

        Instructions are applied one at a time, so a later one can refer to something an
        earlier one in the same turn created: "add a task to send the invoice and also
        move the invoice to friday". Resolving once at plan time reports the second as
        unresolvable and asks the user about a task that by then exists -- a question
        that makes no sense to them.

        Doing it at write time also means the id the domain validates is the id that was
        current when the event was appended, rather than one resolved several steps
        earlier in the same turn.

        The model still never supplies an id. This is the same resolver the planner used,
        run again against fresher state; an id the planner already resolved is kept.
        """
        if proposal.target_id is not None or not proposal.target_ref:
            return proposal
        resolved = resolve_task(proposal.target_ref, self._projection.all_tasks())
        if resolved is None:
            return proposal
        return replace(proposal, target_id=resolved.task_id)

    def _unhandled_count(self, decision: IntentDecision, proposed: int) -> int:
        """Instructions the router found for which no change came back.

        A non-negative answer here is what turns "the planner handled two of the three
        things you asked for" into a sentence the user actually reads. Returning 0 is the
        normal case and costs nothing.
        """
        expected = self._expected_count(decision)
        if expected is None:
            return 0
        return max(0, expected - proposed)

    async def _apply(
        self,
        proposal: ProposedChange,
        correlation_id: CorrelationId,
        actor: Actor,
        trace: Trace,
        known_tasks: Sequence[Task] = (),
    ) -> _Applied:
        """Turn a proposal into committed events.

        The only path from a model's output into the event log, and it goes through the
        domain's invariants every time. The model cannot bypass them because it never
        touches the store.
        """
        causation = trace.turn_id
        fields = proposal.fields

        if proposal.kind is ChangeKind.CREATE_TASK:
            await self._projects.ensure_default(actor=actor, correlation_id=correlation_id)
            title = str(fields["title"])
            # The task stream is new, so it starts at version 0.
            task = await self._tasks.create(
                CreateTask(title=title, project_id=DEFAULT_PROJECT_ID),
                actor=actor,
                correlation_id=correlation_id,
                causation_id=causation,
                idempotency_key=f"{causation}:create:{title}",
            )
            events = await self._since(task.task_id, 0)
            if fields.get("due_date"):
                start = await self._store.stream_version(task.task_id)
                task = await self._tasks.execute(
                    task.task_id,
                    ScheduleTask(due_date=date.fromisoformat(str(fields["due_date"]))),
                    actor=actor,
                    correlation_id=correlation_id,
                    causation_id=causation,
                )
                events += await self._since(task.task_id, start)
            due = f", due {task.due_date.isoformat()}" if task.due_date else ""
            return _Applied(events, f'Added "{task.title}"{due}.',
                            Citation("task", task.title or ""))

        if proposal.kind is ChangeKind.CREATE_EVENT:
            await self._projects.ensure_default(actor=actor, correlation_id=correlation_id)
            title = str(fields["title"])
            starts_at = fields["starts_at"]
            ends_at = fields["ends_at"]
            kind = str(fields.get("kind", "event"))
            location = str(fields.get("location", "")) if fields.get("location") else None
            notes = str(fields.get("notes", "")) if fields.get("notes") else None
            all_day = bool(fields.get("all_day", False))

            # Create the event stream
            from uuid import uuid4

            from swayam.events.catalogue import CALENDAR_EVENT_CREATED
            from swayam.events.envelope import Event as EnvelopeEvent
            event_id = uuid4()
            
            event = EnvelopeEvent(
                type=CALENDAR_EVENT_CREATED.name,
                payload={
                    "title": title,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "kind": kind,
                    "location": location,
                    "notes": notes,
                    "all_day": all_day,
                },
                occurred_at=self._clock.now(),
                actor=actor,
                causation_id=causation,
                correlation_id=correlation_id,
            )

            result = await self._store.append(
                stream_id=event_id,
                stream_type="event",
                expected_version=0,
                events=(event,),
                correlation_id=correlation_id,
            )

            events = result.events
            loc_str = f" at {location}" if location else ""
            return _Applied(
                events,
                f'Scheduled "{title}" ({kind}) from {starts_at} to {ends_at}{loc_str}.',
                Citation("event", title),
            )

        task_id = proposal.target_id
        if task_id is None:
            # Refusing here rather than picking the closest match. The candidates are
            # quoted back so the answer can be one word instead of a rephrasing.
            raise SwayamError(
                f'I could not tell which task you meant by "{proposal.target_ref or ""}". '
                f"Closest: {describe_candidates(proposal.target_ref or '', known_tasks)}."
            )

        start = await self._store.stream_version(task_id)

        if proposal.kind is ChangeKind.SCHEDULE_TASK:
            target = date.fromisoformat(str(fields["due_date"]))
            # Backdating is not enabled for a reschedule: a user asking to move a
            # deadline into the past has almost always misparsed, and invariant I3
            # would rather make them say so than record an impossible commitment.
            before = await self._tasks.get(task_id)
            task = await self._tasks.execute(
                task_id,
                ScheduleTask(due_date=target),
                actor=actor,
                correlation_id=correlation_id,
                causation_id=causation,
            )
            old = before.due_date.isoformat() if before.due_date else "no date"
            new = task.due_date.isoformat() if task.due_date else old
            return _Applied(
                await self._since(task_id, start),
                f'Moved "{task.title}" from {old} to {new}.',
                Citation("task", task.title or ""),
            )

        if proposal.kind is ChangeKind.COMPLETE_TASK:
            task = await self._tasks.execute(
                task_id,
                ChangeStatus(to_status=TaskStatus.COMPLETE),
                actor=actor,
                correlation_id=correlation_id,
                causation_id=causation,
            )
            return _Applied(
                await self._since(task_id, start),
                f'Marked "{task.title}" complete.',
                Citation("task", task.title or ""),
            )

        if proposal.kind is ChangeKind.REOPEN_TASK:
            task = await self._tasks.execute(
                task_id,
                ChangeStatus(to_status=TaskStatus.OPEN),
                actor=actor,
                correlation_id=correlation_id,
                causation_id=causation,
            )
            return _Applied(
                await self._since(task_id, start),
                f'Reopened "{task.title}".',
                Citation("task", task.title or ""),
            )

        if proposal.kind is ChangeKind.BLOCK_TASK:
            blocked_by = str(fields["blocked_by"])
            task = await self._tasks.execute(
                task_id,
                ChangeStatus(to_status=TaskStatus.BLOCKED, blocked_by=blocked_by),
                actor=actor,
                correlation_id=correlation_id,
                causation_id=causation,
            )
            return _Applied(
                await self._since(task_id, start),
                f'Blocked "{task.title}" on {blocked_by}.',
                Citation("task", task.title or ""),
            )

        if proposal.kind is ChangeKind.ARCHIVE_TASK:
            reason = str(fields["reason"]).strip() if fields.get("reason") else None
            task = await self._tasks.execute(
                task_id,
                ArchiveTask(reason=reason),
                actor=actor,
                correlation_id=correlation_id,
                causation_id=causation,
            )
            suffix = f": {reason}" if reason else "."
            return _Applied(
                await self._since(task_id, start),
                f'Archived "{task.title}"{suffix}',
                Citation("task", task.title or ""),
            )

        if proposal.kind is ChangeKind.RENAME_TASK:
            new_title = str(fields["new_title"])
            before = await self._tasks.get(task_id)
            task = await self._tasks.execute(
                task_id,
                RenameTask(new_title=new_title),
                actor=actor,
                correlation_id=correlation_id,
                causation_id=causation,
            )
            events = await self._since(task_id, start)
            if not events:
                # Renaming to the current title is a legitimate no-op, and the domain
                # emits nothing for it. Saying "renamed" would claim a change that never
                # reached the log.
                return _Applied(
                    events,
                    f'"{task.title}" was already called that.',
                    Citation("task", task.title or ""),
                )
            return _Applied(
                events,
                f'Renamed "{before.title}" to "{task.title}".',
                Citation("task", task.title or ""),
            )

        if proposal.kind is ChangeKind.ADD_NOTE:
            body = str(fields["body"])
            task = await self._tasks.execute(
                task_id,
                AddNote(body=body),
                actor=actor,
                correlation_id=correlation_id,
                causation_id=causation,
            )
            return _Applied(
                await self._since(task_id, start),
                f'Noted on "{task.title}".',
                Citation("task", task.title or ""),
            )

        raise SwayamError(f"unsupported change kind: {proposal.kind.value}")

    async def _since(self, stream_id: UUID, from_seq: int) -> tuple[RecordedEvent, ...]:
        """Events appended to a stream after a known version.

        Read-after-append rather than trusting the mutation's return value, so the
        reply's citations and the turn's committed list refer to exactly the events this
        turn caused -- not the whole stream, and not a subset that silently drifts.
        """
        return await self._store.read_stream(stream_id, from_seq=from_seq)

    async def undo(self) -> TurnResult:
        """Undo the last mutation turn by writing a compensating event."""
        trace = Trace()
        correlation_id: CorrelationId = CorrelationId(trace.correlation_id)
        actor = Actor.user(self._user_id)

        if self._last_turn_correlation_id is None:
            return TurnResult(
                trace.turn_id,
                Intent.UNCLEAR,
                Reply(
                    text="Nothing to undo.",
                    clarification="No previous mutation turn recorded in this session.",
                ),
                trace=trace,
            )

        undone_correlation_id = self._last_turn_correlation_id
        undone_event_types = self._last_turn_event_types

        await self._projects.ensure_default(actor=actor, correlation_id=correlation_id)

        event = Event(
            type="task.undone",
            payload={
                "undone_correlation_id": str(undone_correlation_id),
                "undone_event_types": list(undone_event_types),
                "reason": "user requested undo",
            },
            occurred_at=self._clock.now(),
            actor=actor,
            causation_id=trace.correlation_id,
            correlation_id=correlation_id,
        )

        result = await self._store.append(
            stream_id=DEFAULT_PROJECT_ID,
            stream_type="task",
            expected_version=await self._store.stream_version(DEFAULT_PROJECT_ID),
            events=(event,),
            correlation_id=correlation_id,
        )

        # Apply the undo event, then rebuild the projection from the full log
        # to exclude all events from the undone correlation_id.
        for recorded in result.events:
            self._projection.apply(recorded)

        # Rebuild projection from the full event log, which now includes the
        # undo event. This ensures all events from the undone correlation_id
        # are skipped.
        page = await self._store.read_all()
        self._projection.rebuild(page.events)

        self._last_turn_correlation_id = None
        self._last_turn_event_types = ()

        return TurnResult(
            trace.turn_id,
            Intent.MUTATION,
            Reply(
                text=f"Undid the last action ({', '.join(undone_event_types)}).",
            ),
            committed=list(result.events),
            trace=trace,
        )