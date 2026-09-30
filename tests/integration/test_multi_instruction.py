"""Multi-instruction turns: nothing the user asked for goes unmentioned.

The original failure was structural. The planner emitted at most one proposal, so a
sentence containing three requests produced one change and a confident reply that read
as "done" -- and two of the user's instructions vanished with no trace. A user who read
that reply would believe all three had happened.

Every test here asserts on what the *reply* claims as well as on the log, because the
claim is the thing that was wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from swayam.core.clock import FrozenClock
from swayam.runtime import build_runtime

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)  # a Tuesday


@pytest.fixture
async def runtime():
    return await build_runtime(clock=FrozenClock(NOW))


async def ask(runtime, utterance: str):
    return await runtime.orchestrator.handle(utterance)


async def seed_two(runtime):
    await ask(runtime, "add a task to send the q3 report")
    await ask(runtime, "add a task to send the grant draft")


class TestEveryInstructionIsApplied:
    async def test_two_captures_both_land(self, runtime) -> None:
        result = await ask(
            runtime, "add a task to send the invoice, add a task to book the venue"
        )
        assert [e.event.type for e in result.committed] == ["task.created", "task.created"]
        assert sorted(t.title for t in runtime.projection.all_tasks()) == [
            "book the venue", "send the invoice",
        ]
        assert not result.reply.is_clarification

    async def test_a_capture_and_a_completion_both_land(self, runtime) -> None:
        await seed_two(runtime)
        result = await ask(
            runtime, "add a task to send the invoice and also mark the q3 report done"
        )
        assert [e.event.type for e in result.committed] == [
            "task.created", "task.status_changed",
        ]

    async def test_three_instructions_both_land_and_are_all_reported(self, runtime) -> None:
        await seed_two(runtime)
        result = await ask(
            runtime,
            "mark the q3 report done, move the grant draft to friday, "
            "add a task to send the invoice",
        )
        assert len(result.committed) == 3
        # Every change is named in the reply, not just the first.
        assert "send the invoice" in result.reply.text
        assert "q3 report" in result.reply.text
        assert "grant draft" in result.reply.text

    async def test_the_utterance_is_splittable_only_when_every_clause_stands_alone(
        self, runtime
    ) -> None:
        """'the research and interviews' is one title. Splitting it would drop half."""
        result = await ask(runtime, "add a task to write the research and interviews")
        titles = [t.title for t in runtime.projection.all_tasks()]
        assert titles == ["write the research and interviews"]
        assert not result.reply.is_clarification


class TestPartialExecutionIsReported:
    async def test_one_bad_reference_does_not_hide_the_good_instruction(
        self, runtime
    ) -> None:
        """The core regression. The completion succeeds, the completion of a task that
        does not exist fails -- and the reply has to say both."""
        await ask(runtime, "add a task to send the q3 report")
        result = await ask(
            runtime,
            "mark the q3 report done and also mark the nonexistent budget done",
        )

        # The good half really did happen.
        assert [e.event.type for e in result.committed] == ["task.status_changed"]

        # And the user is not told "done" and left to assume the rest worked.
        assert result.reply.is_clarification
        # The failure is named, so the user knows which of the two did not land.
        assert "nonexistent budget" in (result.reply.clarification or "")

    async def test_the_successful_part_is_still_stated_plainly(self, runtime) -> None:
        await ask(runtime, "add a task to send the q3 report")
        result = await ask(
            runtime, "mark the q3 report done and also mark the budget done"
        )
        assert "q3 report" in result.reply.text
        assert result.reply.is_clarification

    async def test_an_ambiguous_reference_reports_both_halves(self, runtime) -> None:
        """Two tasks match 'the report'. The system must not pick one, and must not let
        the successful half paper over that."""
        await ask(runtime, "add a task to send the q3 report")
        await ask(runtime, "add a task to review the q3 report")
        result = await ask(
            runtime, "mark the grant draft done and also mark the q3 report done"
        )
        # Neither half was applied, so there is no "done" text to show -- but both
        # failures are named, so the user can see what was skipped and why.
        assert result.committed == []
        assert result.reply.is_clarification
        clarification = result.reply.clarification or ""
        assert "grant draft" in clarification
        assert "q3 report" in clarification
        # And the ambiguity is spelled out, with the candidates, rather than guessed at.
        assert "could not tell which task" in clarification

    async def test_a_gap_is_reported_even_when_nothing_throws(self, runtime) -> None:
        """'prioritise' has no ChangeKind, so no proposal is produced for it. The
        instruction gap has to surface, or the reply implies it was handled."""
        await ask(runtime, "add a task to send the q3 report")
        result = await ask(
            runtime, "prioritise the q3 report and also mark the q3 report done"
        )
        assert result.committed  # the completion happened
        assert result.reply.is_clarification
        assert "make sense of" in (result.reply.clarification or "")

    async def test_the_clarification_names_the_shortfall_count(self, runtime) -> None:
        await ask(runtime, "add a task to send the q3 report")
        result = await ask(
            runtime, "prioritise the q3 report and also mark the q3 report done"
        )
        assert "1 of the 2" in (result.reply.clarification or "")

    async def test_nothing_applied_reports_the_failure_rather_than_silence(
        self, runtime
    ) -> None:
        result = await ask(runtime, "move the grant draft to friday")
        assert result.committed == []
        assert result.reply.is_clarification
        assert "grant draft" in (result.reply.clarification or "")


class TestOrdering:
    async def test_instructions_are_applied_in_the_order_given(self, runtime) -> None:
        """Order is the user's, not the system's. Renaming then completing, versus the
        reverse, produces different logs."""
        await ask(runtime, "add a task to send the q3 report")
        result = await ask(
            runtime, "rename the q3 report to the board deck and also mark the "
            "board deck done"
        )
        events = [e.event.type for e in result.committed]
        assert events == ["task.renamed", "task.status_changed"]

    async def test_a_later_instruction_sees_an_earlier_one(self, runtime) -> None:
        """Create-then-complete in one sentence must work: the completion resolves
        against the task the same turn created, not against the state before it."""
        result = await ask(
            runtime, "add a task to send the invoice and also mark the invoice done"
        )
        events = [e.event.type for e in result.committed]
        assert events == ["task.created", "task.status_changed"]

    async def test_a_created_task_can_be_scheduled_in_the_same_turn(self, runtime) -> None:
        result = await ask(
            runtime, "add a task to send the invoice and also move the invoice to friday"
        )
        events = [e.event.type for e in result.committed]
        assert events == ["task.created", "task.due_date_set"]
        task = runtime.projection.all_tasks()[0]
        assert task.due_date == datetime(2026, 10, 2, tzinfo=UTC).date()


class TestTrace:
    async def test_a_gap_is_recorded_in_the_trace(self, runtime) -> None:
        """The shortfall has to be observable after the fact, not only in the reply."""
        await ask(runtime, "add a task to send the q3 report")
        result = await ask(
            runtime, "prioritise the q3 report and also mark the q3 report done"
        )
        assert result.trace is not None
        names = [entry.name for entry in result.trace.entries]
        assert "plan_gap" in names

    async def test_a_clean_turn_records_no_gap(self, runtime) -> None:
        result = await ask(runtime, "add a task to send the invoice")
        assert result.trace is not None
        names = [entry.name for entry in result.trace.entries]
        assert "plan_gap" not in names
        assert names == ["route", "plan", "commit"]