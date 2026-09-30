"""Mutation coverage for every ``ChangeKind`` the domain already supports.

Five of the eight kinds existed as enum members with no route to the event log. This
module is the gate that they now all do, because a change kind that is declared but not
wired is a promise the code does not keep.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from swayam.core.clock import FrozenClock
from swayam.domain.task import TaskStatus
from swayam.reasoning.mutation.proposal import ChangeKind
from swayam.runtime import build_runtime

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)  # a Tuesday


@pytest.fixture
async def runtime():
    return await build_runtime(clock=FrozenClock(NOW))


async def ask(runtime, utterance: str):
    return await runtime.orchestrator.handle(utterance)


async def seed(runtime, title: str):
    await ask(runtime, f"add a task to {title}")


async def only_task(runtime):
    tasks = runtime.projection.all_tasks()
    assert len(tasks) == 1, f"expected one task, got {[t.title for t in tasks]}"
    return tasks[0]


class TestReopen:
    async def test_reopen_transitions_complete_back_to_open(self, runtime) -> None:
        await seed(runtime, "send the q3 report")
        await ask(runtime, "mark the q3 report done")
        result = await ask(runtime, "reopen the q3 report")
        assert [e.event.type for e in result.committed] == ["task.status_changed"]
        assert result.committed[0].event.payload["to_status"] == TaskStatus.OPEN.value

    async def test_a_reopened_task_returns_to_the_open_list(self, runtime) -> None:
        await seed(runtime, "send the q3 report")
        await ask(runtime, "mark the q3 report done")
        await ask(runtime, "reopen the q3 report")
        assert [t.title for t in runtime.projection.open_tasks()] == ["send the q3 report"]

    async def test_reopening_a_complete_task_is_allowed_but_closing_twice_is_not(
        self, runtime
    ) -> None:
        """COMPLETE -> OPEN is a legal transition; OPEN -> COMPLETE twice is not."""
        await seed(runtime, "send the q3 report")
        await ask(runtime, "mark the q3 report done")
        await ask(runtime, "reopen the q3 report")
        result = await ask(runtime, "mark the q3 report done")
        assert result.committed
        second = await ask(runtime, "mark the q3 report done")
        assert second.committed == []


class TestBlock:
    async def test_block_records_the_dependency(self, runtime) -> None:
        await seed(runtime, "send the grant draft")
        result = await ask(runtime, "block the grant draft because the budget is late")
        assert [e.event.type for e in result.committed] == ["task.status_changed"]
        assert result.committed[0].event.payload["to_status"] == TaskStatus.BLOCKED.value
        assert result.committed[0].event.payload["blocked_by"] == "the budget is late"

    async def test_a_blocked_task_is_still_live_but_no_longer_open_work(
        self, runtime
    ) -> None:
        """A blocked task is not finished and not archived, so ``is_open`` still holds.
        What changes is that it is no longer *schedulable* work -- the status is the
        signal, and M3's load query is what reads it."""
        await seed(runtime, "send the grant draft")
        await ask(runtime, "block the grant draft because the budget is late")
        task = await only_task(runtime)
        assert task.status is TaskStatus.BLOCKED
        assert task.is_open  # still live
        assert task.blocked_by == "the budget is late"

    async def test_blocking_without_a_reason_asks_rather_than_inventing_one(
        self, runtime
    ) -> None:
        """The domain requires blocked_by. Fabricating one would put a lie in the log."""
        await seed(runtime, "send the grant draft")
        result = await ask(runtime, "block the grant draft")
        assert result.committed == []
        assert result.reply.is_clarification

    async def test_blocking_a_finished_task_is_refused(self, runtime) -> None:
        """COMPLETE -> BLOCKED is not a legal transition: you cannot block work that is
        already finished, and allowing it hides a wrong mental model."""
        await seed(runtime, "send the grant draft")
        await ask(runtime, "mark the grant draft done")
        result = await ask(runtime, "block the grant draft because it is late")
        assert result.committed == []

    @pytest.mark.parametrize(
        ("utterance", "expected"),
        [
            ("move the grant draft", "When should it be due?"),
            ("add a note to the grant draft", "What should the note say?"),
            ("block the grant draft", "What is it blocked on?"),
            ("rename the grant draft", "What should I call it instead?"),
        ],
    )
    async def test_every_required_field_is_asked_for_by_name(
        self, runtime, utterance: str, expected: str
    ) -> None:
        """All five change kinds that need a field. The question used to be a hand-written
        list with two of the five missing, so "move X" and "add a note to X" answered "Which
        task?" while naming the task. The list is now derived from REQUIRED_FIELDS.
        """
        await seed(runtime, "send the grant draft")
        result = await ask(runtime, utterance)
        assert result.committed == []
        assert result.reply.clarification == expected

    @pytest.mark.parametrize(
        "utterance",
        [
            "move the nonexistent thing",
            "add a note to the nonexistent thing",
            "block the nonexistent thing",
        ],
    )
    async def test_an_unresolvable_reference_is_never_answered_with_a_field_question(
        self, runtime, utterance: str
    ) -> None:
        """Asking "when should it be due?" about a task that does not exist presupposes the
        thing the user is about to be told cannot be found. The reference is checked first.
        """
        await seed(runtime, "send the grant draft")
        result = await ask(runtime, utterance)
        assert result.committed == []
        clarification = result.reply.clarification
        assert "could not tell which task" in clarification
        assert "due?" not in clarification
        assert "blocked on?" not in clarification

    async def test_blocking_without_saying_what_it_is_blocked_on_asks(
        self, runtime
    ) -> None:
        """Blocking needs a reason -- the domain rejects an empty one, and inventing
        "waiting on nothing" would write a false dependency into the log.

        The ask has to be about the blocker. It used to say "Which task?" while holding
        the task it had just resolved, which would send the user looking for the wrong
        missing thing."""
        await seed(runtime, "fix the server")
        result = await ask(runtime, "block the fix the server task")
        assert result.committed == []
        assert result.reply.clarification == "What is it blocked on?"

    async def test_unblocking_returns_a_task_to_open_work(self, runtime) -> None:
        """Shipping block without unblock would have been a one-way door. BLOCKED -> OPEN
        is a legal transition, so the round trip has to work."""
        await seed(runtime, "send the grant draft")
        await ask(runtime, "block the grant draft because the budget is late")
        result = await ask(runtime, "unblock the grant draft")
        assert [e.event.type for e in result.committed] == ["task.status_changed"]
        assert result.committed[0].event.payload["to_status"] == TaskStatus.OPEN.value
        task = await only_task(runtime)
        assert task.is_open
        assert task.blocked_by is None

    async def test_unblocking_an_unblocked_task_is_refused(self, runtime) -> None:
        await seed(runtime, "send the grant draft")
        result = await ask(runtime, "unblock the grant draft")
        assert result.committed == []
        assert result.reply.is_clarification


class TestArchive:
    async def test_archive_removes_the_task_from_everywhere(self, runtime) -> None:
        await seed(runtime, "send the old budget draft")
        result = await ask(runtime, "archive the old budget draft")
        assert [e.event.type for e in result.committed] == ["task.archived"]
        assert runtime.projection.open_tasks() == []

    async def test_an_archived_task_cannot_be_archived_again(self, runtime) -> None:
        await seed(runtime, "send the old budget draft")
        await ask(runtime, "archive the old budget draft")
        result = await ask(runtime, "archive the old budget draft")
        assert result.committed == []
        assert result.reply.is_clarification

    async def test_an_archived_task_is_not_a_resolution_target(self, runtime) -> None:
        """Resolving 'the draft' to something the user archived is a silent rewrite of a
        commitment they thought was out of the way."""
        await seed(runtime, "send the old budget draft")
        await ask(runtime, "archive the old budget draft")
        result = await ask(runtime, "mark the old budget draft done")
        assert result.committed == []


class TestRename:
    async def test_rename_emits_old_and_new(self, runtime) -> None:
        await seed(runtime, "send the q3 report")
        result = await ask(runtime, "rename the q3 report to the board deck")
        assert [e.event.type for e in result.committed] == ["task.renamed"]
        payload = result.committed[0].event.payload
        assert payload["old_title"] == "send the q3 report"
        assert payload["new_title"] == "the board deck"

    async def test_the_projection_shows_the_new_title(self, runtime) -> None:
        await seed(runtime, "send the q3 report")
        await ask(runtime, "rename the q3 report to the board deck")
        assert (await only_task(runtime)).title == "the board deck"

    async def test_the_task_keeps_its_identity_across_a_rename(self, runtime) -> None:
        """A rename must not become a delete-and-recreate: the id is the anchor for every
        note, link and claim pointing at this task."""
        await seed(runtime, "send the q3 report")
        before = (await only_task(runtime)).task_id
        await ask(runtime, "rename the q3 report to the board deck")
        assert (await only_task(runtime)).task_id == before

    async def test_the_reply_states_both_titles(self, runtime) -> None:
        await seed(runtime, "send the q3 report")
        result = await ask(runtime, "rename the q3 report to the board deck")
        assert "send the q3 report" in result.reply.text
        assert "the board deck" in result.reply.text


class TestAddNote:
    async def test_note_attaches_to_the_task(self, runtime) -> None:
        await seed(runtime, "send the grant draft")
        result = await ask(
            runtime, "add a note to the grant draft that the budget was approved"
        )
        assert [e.event.type for e in result.committed] == ["task.note_added"]
        assert result.committed[0].event.payload["body"] == "the budget was approved"

    async def test_a_note_survives_on_the_task(self, runtime) -> None:
        await seed(runtime, "send the grant draft")
        await ask(runtime, "add a note to the grant draft that the budget was approved")
        task = await only_task(runtime)
        assert task.notes == ("the budget was approved",)

    async def test_notes_accumulate(self, runtime) -> None:
        await seed(runtime, "send the grant draft")
        await ask(runtime, "add a note to the grant draft that the budget was approved")
        await ask(runtime, "add a note to the grant draft that legal signed off")
        assert len((await only_task(runtime)).notes) == 2

    async def test_a_note_with_no_body_asks(self, runtime) -> None:
        await seed(runtime, "send the grant draft")
        result = await ask(runtime, "add a note to the grant draft")
        assert result.committed == []
        assert result.reply.is_clarification


class TestEveryKindIsReachable:
    """All eight ChangeKinds have a working conversational route, and they compose on one
    task. This is the regression gate for the five that were declared but unreachable.

    The order matters and is not incidental: reopening a task that is not complete, or
    scheduling one that does not exist, is correctly refused. Walking all eight in one
    sequence proves both that each kind works *and* that the transitions are ordered the
    way the domain says they are.
    """

    SEQUENCE: tuple[tuple[ChangeKind, str, str], ...] = (
        (ChangeKind.CREATE_TASK, "add a task to send the q3 report", "task.created"),
        (ChangeKind.COMPLETE_TASK, "mark the q3 report done", "task.status_changed"),
        (ChangeKind.REOPEN_TASK, "reopen the q3 report", "task.status_changed"),
        (ChangeKind.RENAME_TASK, "rename the q3 report to the board deck",
         "task.renamed"),
        (ChangeKind.ADD_NOTE, "add a note to the board deck that it shipped",
         "task.note_added"),
        (ChangeKind.BLOCK_TASK, "block the board deck because legal is slow",
         "task.status_changed"),
        (ChangeKind.SCHEDULE_TASK, "move the board deck to friday", None),
        (ChangeKind.ARCHIVE_TASK, "archive the board deck", "task.archived"),
    )

    async def test_all_eight_kinds_execute_in_one_session(self) -> None:
        runtime = await build_runtime(clock=FrozenClock(NOW))
        for kind, utterance, event_type in self.SEQUENCE:
            result = await runtime.orchestrator.handle(utterance)
            assert result.committed, (
                f"{kind.value} produced no events for {utterance!r}: "
                f"reply={result.reply.text!r} "
                f"clarification={result.reply.clarification!r}"
            )
            assert not result.reply.is_clarification, (
                f"{kind.value} asked for clarification: {result.reply.clarification!r}"
            )
            if event_type is not None:
                assert event_type in [e.event.type for e in result.committed]

        task = await only_task(runtime)
        assert task.archived is True
        assert task.title == "the board deck"
        assert task.notes == ("it shipped",)
        assert task.due_date == datetime(2026, 10, 2, tzinfo=UTC).date()

    def test_every_change_kind_is_covered(self) -> None:
        covered = {kind for kind, _, _ in self.SEQUENCE}
        assert covered == set(ChangeKind), (
            f"uncovered change kinds: {set(ChangeKind) - covered}"
        )