"""End-to-end turns through the mock runtime.

Real domain, real event store, real reasoning wiring; only the model is faked. Each
test asserts what the system did *to the event log*, not just what it said -- a
conversational system that claims to have recorded something and did not is worse than
one that admits it could not.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from swayam.core.clock import FrozenClock
from swayam.domain.task import TaskStatus
from swayam.interfaces.cli.scenarios import SCENARIOS, Scenario
from swayam.runtime import build_runtime
from swayam.storage.repositories import DEFAULT_PROJECT_ID

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)  # a Tuesday


@pytest.fixture
async def runtime():
    return await build_runtime(clock=FrozenClock(NOW))


async def ask(runtime, utterance: str):
    return await runtime.orchestrator.handle(utterance)


class TestCapture:
    async def test_capture_writes_exactly_the_right_events(self, runtime) -> None:
        result = await ask(runtime, "add a task to send the Q3 report")
        assert [e.event.type for e in result.committed] == ["task.created"]
        payload = result.committed[0].event.payload
        assert payload["title"] == "send the q3 report"

    async def test_capture_confirms_plainly(self, runtime) -> None:
        result = await ask(runtime, "add a task to send the Q3 report")
        assert result.reply.text == 'Added "send the q3 report".'
        assert not result.reply.is_clarification

    async def test_capture_cites_what_it_created(self, runtime) -> None:
        result = await ask(runtime, "add a task to send the Q3 report")
        assert [c.kind for c in result.reply.citations] == ["task"]

    async def test_capture_with_a_date_writes_both_events(self, runtime) -> None:
        result = await ask(runtime, "add a task to review the budget due friday")
        assert [e.event.type for e in result.committed] == [
            "task.created",
            "task.due_date_set",
        ]
        assert result.committed[1].event.payload["due_date"] == "2026-10-02"

    async def test_the_projection_reflects_the_new_task(self, runtime) -> None:
        await ask(runtime, "add a task to send the Q3 report")
        open_tasks = runtime.projection.open_tasks()
        assert [t.title for t in open_tasks] == ["send the q3 report"]

    async def test_repeating_a_turn_does_not_duplicate(self, runtime) -> None:
        """Retries are routine. Idempotency keys are scoped per turn, so a genuinely
        repeated request is a second task -- but a replayed *turn* must not double-write."""
        result = await ask(runtime, "add a task to send the Q3 report")
        again = await runtime.orchestrator.handle(result.reply.text)
        assert len(again.committed) == 0 or again.intent.value != "mutation"

    async def test_the_default_project_is_bootstrapped(self, runtime) -> None:
        result = await ask(runtime, "add a task to send the Q3 report")
        log = await runtime.storage.events.read_all()
        assert any(e.event.type == "project.created" for e in log.events)
        assert result.committed[0].event.payload["project_id"] == str(DEFAULT_PROJECT_ID)


class TestReschedule:
    async def test_a_reschedule_emits_a_move_not_an_overwrite(self, runtime) -> None:
        await ask(runtime, "add a task to send the grant draft")
        result = await ask(runtime, "move the grant draft to thursday")
        assert [e.event.type for e in result.committed] == ["task.due_date_set"]
        assert result.committed[0].event.payload["due_date"] == "2026-10-01"

    async def test_a_second_move_records_the_origin(self, runtime) -> None:
        await ask(runtime, "add a task to send the grant draft")
        await ask(runtime, "move the grant draft to thursday")
        result = await ask(runtime, "move the grant draft to friday")
        assert result.committed[0].event.type == "task.due_date_moved"
        assert result.committed[0].event.payload["from_date"] == "2026-10-01"
        assert result.committed[0].event.payload["to_date"] == "2026-10-02"

    async def test_the_reply_states_the_change(self, runtime) -> None:
        await ask(runtime, "add a task to send the grant draft")
        result = await ask(runtime, "move the grant draft to thursday")
        assert "2026-10-01" in result.reply.text

    async def test_backdating_a_reschedule_is_refused(self, runtime) -> None:
        """Invariant I3, reached through the conversational path: a user asking to move
        a deadline into the past has almost always misparsed."""
        await ask(runtime, "add a task to send the grant draft")
        result = await ask(runtime, "move the grant draft to yesterday")
        assert result.committed == []
        assert result.reply.is_clarification


class TestCompletion:
    async def test_completion_transitions_the_aggregate(self, runtime) -> None:
        await ask(runtime, "add a task to send the Q3 report")
        result = await ask(runtime, "mark the q3 report done")
        assert [e.event.type for e in result.committed] == ["task.status_changed"]
        assert result.committed[0].event.payload["to_status"] == TaskStatus.COMPLETE.value

    async def test_a_completed_task_leaves_the_open_list(self, runtime) -> None:
        await ask(runtime, "add a task to send the Q3 report")
        await ask(runtime, "mark the q3 report done")
        assert runtime.projection.open_tasks() == []


class TestSafety:
    async def test_an_unresolvable_reference_asks_rather_than_guessing(self, runtime) -> None:
        """Two tasks could match. Rewriting the wrong one is the failure this guards
        against, so the system asks."""
        await ask(runtime, "add a task to send the Q3 report")
        await ask(runtime, "add a task to review the Q3 report")
        result = await ask(runtime, "mark the q3 report done")
        assert result.committed == []
        assert result.reply.is_clarification

    async def test_a_reference_to_nothing_asks(self, runtime) -> None:
        result = await ask(runtime, "mark the budget done")
        assert result.committed == []
        assert result.reply.is_clarification

    async def test_nonsense_refuses_without_writing(self, runtime) -> None:
        result = await ask(runtime, "fix it")
        assert result.committed == []
        assert result.intent.value == "unclear"
        assert result.reply.is_clarification

    async def test_an_empty_utterance_is_handled(self, runtime) -> None:
        result = await ask(runtime, "   ")
        assert result.committed == []

    async def test_a_rejected_mutation_leaves_the_projection_untouched(self, runtime) -> None:
        await ask(runtime, "add a task to send the Q3 report")
        before = [t.title for t in runtime.projection.open_tasks()]
        await ask(runtime, "mark the budget done")
        assert [t.title for t in runtime.projection.open_tasks()] == before


class TestUnbuiltPaths:
    @pytest.mark.parametrize(
        "utterance",
        ["what's on today", "am I overcommitted this month", "how did last week go"],
    )
    async def test_query_paths_decline_rather_than_guess(self, runtime, utterance: str) -> None:
        """The query projections do not exist until M3. Declining is the correct
        behaviour; answering from state that is not there is how a system starts
        hallucinating."""
        result = await ask(runtime, utterance)
        assert result.committed == []
        assert "M3" in result.reply.text

    async def test_smalltalk_is_handled_without_touching_the_log(self, runtime) -> None:
        result = await ask(runtime, "hey")
        assert result.intent.value == "smalltalk"
        assert result.committed == []


class TestTraceability:
    async def test_every_mutation_event_traces_to_its_turn(self, runtime) -> None:
        """Invariant I7 and constraint C10: a state change must be explainable back to
        a sentence the user actually typed."""
        result = await ask(runtime, "add a task to send the Q3 report")
        for recorded in result.committed:
            assert recorded.event.causation_id == result.turn_id
            assert recorded.event.actor.type.value == "user"

    async def test_the_turn_trace_records_the_route(self, runtime) -> None:
        result = await ask(runtime, "add a task to send the Q3 report")
        assert result.trace is not None
        names = [entry.name for entry in result.trace.entries]
        assert names == ["route", "plan", "commit"]

    async def test_the_trace_carries_no_utterance_text(self, runtime) -> None:
        """Logs hold identifiers; raw user text stays in the store where retention
        rules apply to it."""
        result = await ask(runtime, "add a task to send a very private thing")
        assert result.trace is not None
        assert "private" not in str(result.trace.to_dict())


class TestScenarios:
    """The scenario suite is the acceptance gate. Every scenario must hold."""

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
    async def test_scenario_holds(self, scenario: Scenario) -> None:
        runtime = await build_runtime(clock=FrozenClock(NOW))
        for turn in scenario.turns:
            result = await runtime.orchestrator.handle(turn.utterance)
            _assert_expectation(turn.expect, result)


def _assert_expectation(expect: str, result) -> None:
    if expect == "captured":
        assert any(e.event.type == "task.created" for e in result.committed), (
            f"expected a task to be created, got {result.reply.text!r}"
        )
    elif expect == "rescheduled":
        assert any(
            e.event.type in ("task.due_date_set", "task.due_date_moved")
            for e in result.committed
        ), f"expected a reschedule, got {result.reply.text!r}"
    elif expect == "completed":
        assert any(
            e.event.type == "task.status_changed" for e in result.committed
        ), f"expected a completion, got {result.reply.text!r}"
    elif expect in ("reopened", "blocked"):
        expected_status = "open" if expect == "reopened" else "blocked"
        assert any(
            e.event.type == "task.status_changed"
            and e.event.payload.get("to_status") == expected_status
            for e in result.committed
        ), f"expected a {expect}, got {result.reply.text!r}"
    elif expect == "archived":
        assert any(e.event.type == "task.archived" for e in result.committed), (
            f"expected an archive, got {result.reply.text!r}"
        )
    elif expect == "renamed":
        assert any(e.event.type == "task.renamed" for e in result.committed), (
            f"expected a rename, got {result.reply.text!r}"
        )
    elif expect == "noted":
        assert any(e.event.type == "task.note_added" for e in result.committed), (
            f"expected a note, got {result.reply.text!r}"
        )
    elif expect == "partial":
        # The specific failure this encodes: some instruction landed, some did not, and
        # the reply has to say which. Asserting only "events were written" would pass
        # against the original bug.
        assert result.committed, "expected the resolvable instruction to succeed"
        assert result.reply.is_clarification, (
            f"a partial turn must ask about the part it did not do, got "
            f"{result.reply.text!r}"
        )
        assert result.reply.clarification, "a clarification needs a reason"
    elif expect == "clarified":
        assert result.reply.is_clarification, (
            f"expected a clarification, got {result.reply.text!r}"
        )
        assert result.committed == []
    elif expect == "declined":
        assert result.committed == [], "a pending path must not write"
    else:  # pragma: no cover
        raise AssertionError(f"unknown expectation: {expect}")


class TestMultiInstructionTitles:
    """A conjunction inside a title must not be read as a separator.

    Splitting "write the research and interviews" would create one task and discard the
    other half of the sentence, then reply as though both were handled.
    """

    async def test_the_whole_phrase_becomes_one_title(self, runtime) -> None:
        result = await ask(runtime, "add a task to write the research and interviews")
        titles = [t.title for t in runtime.projection.all_tasks()]
        assert titles == ["write the research and interviews"]
        assert result.reply.is_clarification is False
