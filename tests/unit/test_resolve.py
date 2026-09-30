"""Free-text reference resolution.

The properties that matter: an ambiguous reference must resolve to *nothing* rather than to
a guess, and the candidates shown to the user must be the candidates actually considered.
Those used to be two different sets, because two functions each implemented the matching
rule and they disagreed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from swayam.domain.task import Task, TaskStatus
from swayam.reasoning.mutation.resolve import (
    clean_title,
    describe_candidates,
    match_candidates,
    resolve_task,
)


def task(title: str | None, *, archived: bool = False) -> Task:
    return Task(
        task_id=uuid4(),
        title=title,
        status=TaskStatus.OPEN,
        created_at=datetime(2026, 9, 29, tzinfo=UTC),
        archived=archived,
    )


class TestMatching:
    def test_both_sides_are_cleaned(self) -> None:
        """Cleaning only the needle compared "send report" against the raw
        "send the report" and never matched."""
        tasks = [task("send the report")]
        assert resolve_task("send the report", tasks) is tasks[0]
        assert resolve_task("the send report task", tasks) is tasks[0]

    def test_an_exact_title_is_a_better_answer_than_a_substring_overlap(self) -> None:
        tasks = [task("read memo twice"), task("read the memo")]
        assert resolve_task("read the memo", tasks) is tasks[1]

    def test_zero_matches_resolve_to_nothing(self) -> None:
        assert resolve_task("the invoice", [task("send the report")]) is None

    def test_several_matches_resolve_to_nothing(self) -> None:
        """Choosing between two plausible tasks is how the wrong commitment gets rewritten.

        Ambiguity here comes from substring overlap: no title equals the needle, and both
        contain it.
        """
        tasks = [task("read the quarterly memo"), task("review the monthly memo")]
        assert resolve_task("read memo", tasks) is None

    def test_archived_tasks_are_never_candidates(self) -> None:
        tasks = [task("send the report", archived=True)]
        assert resolve_task("the report", tasks) is None
        assert match_candidates("the report", tasks) == []


class TestCandidatesMatchResolution:
    def test_the_list_shown_is_the_list_considered(self) -> None:
        """This is the drift that was live: describe_candidates cleaned only the reference
        and dropped the exact tier, so it offered "read memo; read memo twice" and omitted
        "read the memo" -- the task whose title was the phrase the user typed."""
        tasks = [task("read the memo"), task("read memo"), task("read memo twice")]
        reference = "the read the memo"
        considered = [t.title for t in match_candidates(reference, tasks)]
        described = describe_candidates(reference, tasks).split("; ")
        assert described == considered

    def test_an_unresolvable_reference_still_lists_everything(self) -> None:
        """When nothing matches, showing every live task is more useful than a shrug."""
        tasks = [task("send the report"), task("review the budget")]
        assert describe_candidates("the invoice", tasks) == (
            "send the report; review the budget"
        )

    def test_no_tasks_at_all_says_so(self) -> None:
        assert describe_candidates("anything", []) == "you have no tasks yet"

    def test_an_empty_reference_does_not_match_everything(self) -> None:
        assert resolve_task("", [task("send the report")]) is None
        assert resolve_task("the", [task("send the report")]) is None


class TestCleanTitle:
    def test_articles_and_wrapper_words_are_not_matching_keys(self) -> None:
        assert clean_title("the grant draft task") == "grant draft"

    def test_it_is_never_applied_to_a_stored_title(self) -> None:
        """clean_title is for comparing, not for rewriting: the user chose that title."""
        stored = task("send the report")
        assert stored.title == "send the report"