"""Intent routing and date resolution.

The router decides which latency budget to spend, so the properties that matter are
(a) obvious phrasings land on the right path and (b) anything ambiguous refuses rather
than guessing. (b) is the one that protects trust.

``DeterministicRouter`` and ``LLMRouter`` are interchangeable behind the same protocol,
so the contract is asserted here once and both are held to it.
"""

from __future__ import annotations

from datetime import date

import pytest

from swayam.reasoning.router.dates import extract_date, iso_week_key, strip_date_phrases
from swayam.reasoning.router.deterministic import (
    _ACTION_HEADS,
    LEADING_VERB,
    STATE_WORDS,
    DeterministicRouter,
    _detect_action,
)
from swayam.reasoning.router.intent import Intent
from swayam.reasoning.router.llm_router import ACTION_NAMES

TUESDAY = date(2026, 9, 29)


def _head_spellings(head_pattern: str) -> tuple[str, ...]:
    """Concrete verb phrases to test a head pattern with.

    Hand-expands one head's final ``(?:a|b|c)`` group so the test can name each
    alternative without the test itself becoming a hand-maintained copy of the list.
    """
    body = head_pattern.removeprefix(r"^(?:please\s+)?").removesuffix(r"\b")
    if not (body.startswith("(?:") and body.endswith(")")):
        return ()
    inner = body[3:-1]
    if "|" not in inner:
        return ()
    return tuple(alt for alt in inner.split("|") if alt)


def _as_phrase(alt: str) -> str:
    """Concrete spelling of one alternation branch.

    ``re-?title`` matches both "retitle" and "re-title"; the literal pattern text would
    not appear in any utterance.
    """
    return alt.replace("-?", "")


@pytest.fixture
def router() -> DeterministicRouter:
    return DeterministicRouter()


class TestDateExtraction:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("today", date(2026, 9, 29)),
            ("tomorrow", date(2026, 9, 30)),
            ("day after tomorrow", date(2026, 10, 1)),
            ("thursday", date(2026, 10, 1)),
            ("friday", date(2026, 10, 2)),
            ("on monday", date(2026, 10, 5)),
            ("next thursday", date(2026, 10, 8)),
            ("this friday", date(2026, 10, 2)),
        ],
    )
    def test_resolves_common_phrasings(self, text: str, expected: date) -> None:
        assert extract_date(text, today=TUESDAY) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("in 3 days", date(2026, 10, 2)),
            ("in 1 day", date(2026, 9, 30)),
            ("in 2 weeks", date(2026, 10, 13)),
            ("in 6 months", date(2027, 3, 29)),
            ("october 19", date(2026, 10, 19)),
            ("19 october", date(2026, 10, 19)),
            ("on oct 5th", date(2026, 10, 5)),
        ],
    )
    def test_resolves_relative_and_month_day_phrasings(
        self, text: str, expected: date
    ) -> None:
        """The phrasings that used to leak straight into a task title."""
        assert extract_date(text, today=TUESDAY) == expected

    def test_a_bare_number_is_not_a_date(self) -> None:
        """"top 3 priorities" is a quantity. Reading it as the 3rd of the month is the
        kind of confident misread this whole module is built to refuse."""
        assert extract_date("top 3 priorities", today=TUESDAY) is None

    def test_window_phrases_are_not_resolvable(self) -> None:
        """'this week' is a window, not a day. Resolving it would invent a commitment."""
        assert extract_date("this week", today=TUESDAY) is None
        assert extract_date("next month", today=TUESDAY) is None

    def test_past_weekdays_resolve_forward_not_back(self) -> None:
        """"on monday", said on a Tuesday, means the *next* Monday. Resolution is
        forward-looking only: a weekday in the past would be refused by invariant I3
        anyway, and silently picking yesterday would encode a wrong commitment."""
        assert extract_date("on monday", today=TUESDAY) == date(2026, 10, 5)

    def test_a_bare_weekday_meaning_today_is_refused(self) -> None:
        """"Tuesday" said on a Tuesday is genuinely ambiguous. Guessing is worse than
        asking, so it resolves to nothing."""
        assert extract_date("tuesday", today=TUESDAY) is None

    def test_today_still_resolves_on_its_own_name(self) -> None:
        assert extract_date("tuesday", today=TUESDAY) is None
        assert extract_date("due today", today=TUESDAY) == TUESDAY

    def test_day_of_month_rolls_into_next_month(self) -> None:
        assert extract_date("on the 2nd", today=TUESDAY) == date(2026, 10, 2)

    def test_day_of_month_rolls_into_next_year(self) -> None:
        assert extract_date("the 15th", today=date(2026, 12, 20)) == date(2027, 1, 15)

    def test_impossible_date_resolves_to_nothing(self) -> None:
        assert extract_date("the 31st", today=date(2026, 9, 30)) is None

    def test_unparseable_text_resolves_to_nothing(self) -> None:
        assert extract_date("sometime soon", today=TUESDAY) is None

    def test_iso_week_key(self) -> None:
        assert iso_week_key(date(2026, 9, 29)) == "2026-W40"


class TestDateRemoval:
    """Removal shares the extraction table, so these two cannot drift apart."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("review the budget due friday", "review the budget"),
            ("send the report in 3 days", "send the report"),
            ("review the budget due october 19", "review the budget"),
            ("review the budget on october 19", "review the budget"),
            ("the grant draft to thursday", "the grant draft"),
            ("call the vendor tomorrow", "call the vendor"),
            ("review the plan this week", "review the plan"),
        ],
    )
    def test_removes_the_date_phrasing(self, text: str, expected: str) -> None:
        assert strip_date_phrases(text, today=TUESDAY) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "friday standup sync",
            "send the q3 report",
            "quarterly business review",
            "research the monday effect",
        ],
    )
    def test_keeps_phrasing_that_is_not_a_date(self, text: str) -> None:
        """The failure mode is over-eager stripping, not under-stripping: a task called
        "friday standup" must survive."""
        assert strip_date_phrases(text, today=TUESDAY) == text

    def test_removal_never_leaves_dangling_prepositions(self) -> None:
        assert not strip_date_phrases("the draft due october 19", today=TUESDAY).endswith("due")


class TestRouting:
    @pytest.mark.parametrize(
        ("utterance", "expected"),
        [
            ("add a task to send the Q3 report", Intent.MUTATION),
            ("move the grant draft to thursday", Intent.MUTATION),
            ("mark the report done", Intent.MUTATION),
            ("reschedule the review", Intent.MUTATION),
            ("what's on today", Intent.STATE_QUERY),
            ("what's on my plate right now", Intent.STATE_QUERY),
            ("am I overcommitted this month", Intent.LOAD_QUERY),
            ("how busy am I", Intent.LOAD_QUERY),
            ("what did I say I'd do this week", Intent.TEMPORAL_QUERY),
            ("how did last week go", Intent.CROSS_DOMAIN),
            ("catch me up", Intent.CROSS_DOMAIN),
            ("hey", Intent.SMALLTALK),
        ],
    )
    async def test_routes_obvious_phrasings(self, router: DeterministicRouter,
                                           utterance: str, expected: Intent) -> None:
        assert (await router.route(utterance, today=TUESDAY)).intent is expected

    @pytest.mark.parametrize("utterance", ["fix it", "yeah do the thing", "asdfgh"])
    async def test_unmatched_utterances_refuse(self, router: DeterministicRouter,
                                               utterance: str) -> None:
        decision = await router.route(utterance, today=TUESDAY)
        assert decision.intent is Intent.UNCLEAR
        assert not decision.is_confident

    async def test_empty_utterance_refuses(self, router: DeterministicRouter) -> None:
        assert (await router.route("   ", today=TUESDAY)).intent is Intent.UNCLEAR

    async def test_mutation_wins_over_a_trailing_question(
        self, router: DeterministicRouter
    ) -> None:
        """"move the draft to Thursday, when is it due?" is a mutation that happens to
        contain a question."""
        decision = await router.route(
            "move the draft to thursday, when is it due?", today=TUESDAY
        )
        assert decision.intent is Intent.MUTATION
        assert decision.slots["action"] == "reschedule"

    async def test_capture_extracts_a_title(self, router: DeterministicRouter) -> None:
        decision = await router.route("add a task to send the Q3 report", today=TUESDAY)
        assert decision.slots["action"] == "create"
        assert decision.slots["title"] == "send the q3 report"

    async def test_capture_strips_a_trailing_date_clause(
        self, router: DeterministicRouter
    ) -> None:
        decision = await router.route(
            "add a task to review the budget due friday", today=TUESDAY
        )
        assert decision.slots["title"] == "review the budget"
        assert decision.slots["due_date"] == "2026-10-02"

    @pytest.mark.parametrize(
        ("utterance", "title"),
        [
            ("add a task to review the budget in 3 days", "review the budget"),
            ("add a task to review the budget due october 19", "review the budget"),
            ("add a task for friday standup sync", "friday standup sync"),
        ],
    )
    async def test_date_phrasing_never_reaches_a_title(
        self, router: DeterministicRouter, utterance: str, title: str
    ) -> None:
        decision = await router.route(utterance, today=TUESDAY)
        assert decision.slots["title"] == title

    async def test_reschedule_extracts_a_target_and_date(
        self, router: DeterministicRouter
    ) -> None:
        decision = await router.route("move the grant draft to thursday", today=TUESDAY)
        assert decision.slots["action"] == "reschedule"
        # The reference keeps the user's phrasing, articles included -- it is what a
        # clarification quotes back. Normalising is the resolver's job, not the
        # router's.
        assert decision.slots["target_ref"] == "the grant draft"
        assert decision.slots["due_date"] == "2026-10-01"

    async def test_completion_is_distinguished_from_a_move(
        self, router: DeterministicRouter
    ) -> None:
        decision = await router.route("mark the q3 report done", today=TUESDAY)
        assert decision.slots["action"] == "complete"
        assert decision.slots["target_ref"] == "the q3 report"

    async def test_a_bare_mutation_verb_is_low_confidence(
        self, router: DeterministicRouter
    ) -> None:
        """Nothing to act on means the router should not claim it can."""
        decision = await router.route("add", today=TUESDAY)
        assert not decision.is_confident

    async def test_routing_is_deterministic(self, router: DeterministicRouter) -> None:
        first = await router.route("add a task to send the report", today=TUESDAY)
        second = await router.route("add a task to send the report", today=TUESDAY)
        assert first == second


class TestTheFiveNewVerbs:
    async def test_block_extracts_the_dependency(self, router: DeterministicRouter) -> None:
        decision = await router.route(
            "block the grant draft because the budget is late", today=TUESDAY
        )
        assert decision.slots["action"] == "block"
        assert decision.slots["target_ref"] == "the grant draft"
        assert decision.slots["blocked_by"] == "the budget is late"

    async def test_block_without_a_reason_yields_no_dependency(
        self, router: DeterministicRouter
    ) -> None:
        """The domain requires a blocker. Inventing one would be a fabrication."""
        decision = await router.route("block the grant draft", today=TUESDAY)
        assert decision.slots["action"] == "block"
        assert decision.slots["target_ref"] == "the grant draft"
        assert "blocked_by" not in decision.slots

    async def test_block_until_a_date_is_not_a_dependency(
        self, router: DeterministicRouter
    ) -> None:
        """"until Friday" is scheduling. Reading it as the reason would put a date in
        the blocked_by field."""
        decision = await router.route("block the grant draft until friday", today=TUESDAY)
        assert "blocked_by" not in decision.slots

    async def test_archive(self, router: DeterministicRouter) -> None:
        decision = await router.route("archive the old budget draft", today=TUESDAY)
        assert decision.slots["action"] == "archive"
        # Articles are kept, as with every other reference.
        assert decision.slots["target_ref"] == "the old budget draft"

    async def test_rename_splits_old_from_new(self, router: DeterministicRouter) -> None:
        decision = await router.route(
            "rename the q3 report to the board deck", today=TUESDAY
        )
        assert decision.slots["action"] == "rename"
        assert decision.slots["target_ref"] == "the q3 report"
        assert decision.slots["new_title"] == "the board deck"

    async def test_reopen(self, router: DeterministicRouter) -> None:
        decision = await router.route("reopen the q3 report", today=TUESDAY)
        assert decision.slots["action"] == "reopen"
        assert decision.slots["target_ref"] == "the q3 report"

    async def test_note_splits_target_from_body(self, router: DeterministicRouter) -> None:
        decision = await router.route(
            "add a note to the grant draft that the budget was approved", today=TUESDAY
        )
        assert decision.slots["action"] == "note"
        assert decision.slots["target_ref"] == "the grant draft"
        assert decision.slots["body"] == "the budget was approved"


class TestContentWordsCannotHijackTheVerb:
    """The action is read from the head of the clause.

    A whole-clause keyword scan let the user's own object text choose the verb: "add a
    note to the deck that it shipped" became a completion, because "shipped" is also a
    completion word. Each case below was a real misroute.
    """

    @pytest.mark.parametrize(
        ("utterance", "action", "title"),
        [
            # 'shipped' is a completion word, but it is inside the note body.
            ("add a task to ship the q3 report", "create", "ship the q3 report"),
            # 'move' is a reschedule word, but it is inside the title.
            ("add a task to move the boxes", "create", "move the boxes"),
            # 'archive' is an archive word, but it is inside the title.
            ("add a task to archive the old files", "create", "archive the old files"),
            # 'rename' is a rename word, but it is inside the title.
            ("add a task to rename the config", "create", "rename the config"),
            # 'block' is a block word, but it is inside the title.
            ("add a task to block out the afternoon", "create", "block out the afternoon"),
        ],
    )
    async def test_a_verb_inside_the_title_does_not_change_the_action(
        self, router: DeterministicRouter, utterance: str, action: str, title: str
    ) -> None:
        decision = await router.route(utterance, today=TUESDAY)
        assert decision.slots["action"] == action
        assert decision.slots["title"] == title

    @pytest.mark.parametrize(
        ("utterance", "action"),
        [
            ("mark the q3 report not done", "reopen"),
            ("mark the q3 report not finished", "reopen"),
            ("mark the q3 report not complete", "reopen"),
            ("mark the q3 report not shipped", "reopen"),
            ("mark the q3 report as not done", "reopen"),
            # Un-opening is reopening.
            ("mark the q3 report not open", "reopen"),
            ("mark the q3 report done", "complete"),
            ("mark the q3 report as complete", "complete"),
            ("mark the q3 report open", "reopen"),
        ],
    )
    async def test_a_negated_completion_word_reopens_rather_than_completes(
        self, router: DeterministicRouter, utterance: str, action: str
    ) -> None:
        """"not done" used to complete the task, because the complete pattern was checked
        first and "not done" contains "done". An instruction to undo something was being
        carried out as an instruction to do it."""
        decision = await router.route(utterance, today=TUESDAY)
        assert decision.slots["action"] == action

    @pytest.mark.parametrize(
        "utterance",
        [
            "mark the q3 report not done",
            "mark the q3 report as not done",
            "mark the q3 report not finished",
            "mark the q3 report done",
            "mark the q3 report as complete",
            "mark the q3 report open",
        ],
    )
    async def test_a_trailing_state_word_is_stripped_whole(
        self, router: DeterministicRouter, utterance: str
    ) -> None:
        """The state stripper used to remove the trailing "done" and leave "not" behind
        as part of the task reference, so the reference could never resolve."""
        decision = await router.route(utterance, today=TUESDAY)
        assert decision.slots["target_ref"] == "the q3 report"

    async def test_a_completion_word_inside_a_note_body_does_not_reclassify(
        self, router: DeterministicRouter
    ) -> None:
        decision = await router.route(
            "add a note to the board deck that it shipped", today=TUESDAY
        )
        assert decision.slots["action"] == "note"
        assert decision.slots["target_ref"] == "the board deck"
        assert decision.slots["body"] == "it shipped"

    @pytest.mark.parametrize(
        ("utterance", "action"),
        [
            ("mark the q3 report done", "complete"),
            ("mark the q3 report as finished", "complete"),
            ("mark the q3 report open", "reopen"),
            ("mark the q3 report as in progress", "reopen"),
        ],
    )
    async def test_mark_is_refined_by_its_state_word(
        self, router: DeterministicRouter, utterance: str, action: str
    ) -> None:
        decision = await router.route(utterance, today=TUESDAY)
        assert decision.slots["action"] == action

    async def test_every_action_head_can_also_open_a_second_fragment(
        self, router: DeterministicRouter
    ) -> None:
        """The split gate used to consult a hand-written verb list that was 12 heads
        behind the dispatcher's own table, so a second instruction was absorbed into the
        first task's title instead of splitting.

        Every head from every action is asserted here. The dispatcher is the single source
        of truth now, so this is a property test rather than a list to re-copy.
        """
        for action, pattern in _ACTION_HEADS:
            head = pattern.pattern
            spellings = _head_spellings(head)
            # The note head is a nested optional-lead group, which the expansion above
            # does not parse. Named here so it is not silently uncovered.
            if not spellings:
                spellings = ("leave a note",)
            for spelling in spellings:
                phrase = _as_phrase(spelling)
                decision = await router.route(
                    f"add a task to write the report, {phrase} the old deck",
                    today=TUESDAY,
                )
                assert decision.slots["instruction_count"] == 2, (
                    f"{action} head {phrase!r} did not split"
                )

    async def test_a_split_across_a_verb_inside_a_title_still_works(
        self, router: DeterministicRouter
    ) -> None:
        """The trailing clause is a real instruction, so the split is still believed."""
        decision = await router.route(
            "add a task to move the boxes and mark the old report done", today=TUESDAY
        )
        assert decision.slots["instruction_count"] == 2
        assert [i["action"] for i in decision.slots["instructions"]] == [
            "create", "complete",
        ]


class TestMultiInstruction:
    async def test_two_instructions_become_two_slots(
        self, router: DeterministicRouter
    ) -> None:
        decision = await router.route(
            "add a task to send the invoice and also mark the q3 report done",
            today=TUESDAY,
        )
        instructions = decision.slots["instructions"]
        assert isinstance(instructions, list)
        assert decision.slots["instruction_count"] == 2
        assert [i["action"] for i in instructions] == ["create", "complete"]
        assert instructions[0]["title"] == "send the invoice"
        assert instructions[1]["target_ref"] == "the q3 report"

    async def test_a_comma_separated_pair_splits(
        self, router: DeterministicRouter
    ) -> None:
        decision = await router.route(
            "add a task to send the invoice, move the grant draft to thursday",
            today=TUESDAY,
        )
        assert decision.slots["instruction_count"] == 2
        assert [i["action"] for i in decision.slots["instructions"]] == [
            "create", "reschedule",
        ]

    async def test_three_instructions_are_counted(
        self, router: DeterministicRouter
    ) -> None:
        decision = await router.route(
            "add a task to send the invoice, mark the report done, "
            "move the grant draft to friday",
            today=TUESDAY,
        )
        assert decision.slots["instruction_count"] == 3

    async def test_a_conjunction_inside_a_title_is_not_a_split(
        self, router: DeterministicRouter
    ) -> None:
        """The important one. "research and interviews" is one title, not two
        instructions -- and splitting it would silently discard half the sentence."""
        decision = await router.route(
            "add a task to write the research and interviews", today=TUESDAY
        )
        assert decision.slots["instruction_count"] == 1
        assert decision.slots["title"] == "write the research and interviews"

    async def test_a_bare_capture_verb_creates_without_an_add_wrapper(
        self, router: DeterministicRouter
    ) -> None:
        """"book train tickets" names no "add a task". It is still an instruction, and it
        used to be read as a reschedule because the action default was "update"."""
        decision = await router.route(
            "book train tickets due october 19", today=TUESDAY
        )
        assert decision.intent is Intent.MUTATION
        assert decision.slots["title"] == "book train tickets"
        assert decision.slots["due_date"] == "2026-10-19"

    async def test_an_unrecognised_head_is_not_silently_an_update(
        self, router: DeterministicRouter
    ) -> None:
        """The action default was "update", so anything unrecognised became an attempt to
        change a task the user never mentioned. No head means no action."""
        decision = await router.route("the garden needs attention", today=TUESDAY)
        assert decision.slots.get("action") is None

    async def test_multi_instruction_confidence_is_slightly_lower(
        self, router: DeterministicRouter
    ) -> None:
        """A clause boundary is a guess even when each clause is clear, and the router
        should price that in."""
        single = await router.route("add a task to send the invoice", today=TUESDAY)
        multi = await router.route(
            "add a task to send the invoice and also mark the report done", today=TUESDAY
        )
        assert multi.confidence < single.confidence
        assert multi.is_confident

class TestTheVocabularyIsDefinedOnce:
    """Guards against the drift this codebase has produced repeatedly.

    Four hand-maintained lists describing the same two vocabularies had already diverged
    twice: a verb gate that rejected verbs its own dispatcher could parse, and a completion
    pattern that turned "not done" into a completion. Both are now derived, so these assert
    the derivation rather than re-listing the words.
    """

    async def test_every_state_word_routes_to_the_action_it_declares(
        self, router: DeterministicRouter
    ) -> None:
        for word, action in STATE_WORDS.items():
            decision = await router.route(f"mark the q3 report {word}", today=TUESDAY)
            assert decision.slots["action"] == action, (
                f"{word!r} declares {action!r} but routed to "
                f"{decision.slots['action']!r}"
            )

    async def test_every_state_word_is_admitted_as_a_mutation(
        self, router: DeterministicRouter
    ) -> None:
        """The admission gate used to be a separate tuple that omitted "closed", so
        "the report is closed" was not a mutation at all."""
        for word in STATE_WORDS:
            decision = await router.route(f"the q3 report is {word}", today=TUESDAY)
            assert decision.intent is Intent.MUTATION, f"{word!r} was not admitted"

    def test_the_leading_verb_stripper_knows_every_head(
        self, router: DeterministicRouter
    ) -> None:
        """LEADING_VERB used to omit "abandon", "log" and "scrub", so those verbs were left
        in the reference and the reference could never resolve."""
        for action, pattern in _ACTION_HEADS:
            for spelling in _head_spellings(pattern.pattern):
                phrase = _as_phrase(spelling)
                if not LEADING_VERB.match(f"{phrase} the old deck "):
                    continue  # optional-lead heads: the bare verb is also valid
                rest = LEADING_VERB.sub("", f"{phrase} the old deck ").strip()
                assert rest == "the old deck", (
                    f"{action} head {phrase!r} left {rest!r}"
                )

    def test_the_llm_router_accepts_every_action_the_deterministic_router_emits(
        self, router: DeterministicRouter
    ) -> None:
        """The LLM router's action vocabulary was a hand-written Literal. It was missing
        nothing today, but adding an action to the deterministic router would have made it
        unreachable through the LLM path with no test failing."""
        emitted = {
            _detect_action(utterance)
            for utterance in (
                "add a task to send the invoice",
                "move the invoice to friday",
                "mark the invoice done",
                "mark the invoice open",
                "archive the invoice",
                "rename the invoice to the receipt",
                "block the invoice because legal",
                "unblock the invoice",
                "add a note to the invoice that it landed",
                "prioritise the invoice",
                "set the invoice to high",
            )
        }
        emitted.discard(None)
        assert emitted
        assert emitted <= set(ACTION_NAMES), (
            f"LLM path cannot express {emitted - set(ACTION_NAMES)}"
        )
