"""The LLM-backed router and planner, driven through ``MockLLM``.

These tests are about the laundering, not about the model. The mock returns fixed
payloads so the behaviour under test is the *checking* code: grounding, field
allowlisting, index bounds, required fields, and the fact that a reference is resolved
in this process rather than accepted from the model.

A test that only asserted "the fixture came back" would pass against an implementation
that trusted the model completely. Each test below fails if that trust creeps back in.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest

from swayam.domain.task import Task, TaskStatus
from swayam.llm.mock import MockLLM
from swayam.reasoning.mutation.llm_planner import PLANNER_PURPOSE, LLMPlanner
from swayam.reasoning.mutation.proposal import ChangeKind
from swayam.reasoning.mutation.rule_planner import MutationPlanner, RuleBasedPlanner
from swayam.reasoning.router.deterministic import DeterministicRouter, IntentRouter
from swayam.reasoning.router.intent import Intent
from swayam.reasoning.router.llm_router import ROUTER_PURPOSE, LLMRouter

TODAY = date(2026, 9, 29)  # a Tuesday

REPORT_ID = UUID("11111111-1111-1111-1111-111111111111")
DRAFT_ID = UUID("22222222-2222-2222-2222-222222222222")
OTHER_PROJECT = UUID("00000000-0000-0000-0000-000000000099")


@pytest.fixture
def known_tasks() -> list[Task]:
    return [
        Task(task_id=REPORT_ID, title="send the q3 report", status=TaskStatus.OPEN),
        Task(task_id=DRAFT_ID, title="send the grant draft", status=TaskStatus.OPEN),
    ]


def llm_with(router_payload=None, plan_payload=None) -> MockLLM:
    llm = MockLLM()
    if router_payload is not None:
        llm.register(ROUTER_PURPOSE, router_payload)
    if plan_payload is not None:
        llm.register(PLANNER_PURPOSE, plan_payload)
    return llm


class TestProtocolConformance:
    """Both implementations are held to the same protocols, so the orchestrator cannot
    tell them apart. This is what makes the swap a runtime flag rather than a rewrite."""

    def test_llm_router_satisfies_the_router_protocol(self) -> None:
        assert isinstance(LLMRouter(MockLLM()), IntentRouter)

    def test_llm_planner_satisfies_the_planner_protocol(self) -> None:
        assert isinstance(LLMPlanner(MockLLM()), MutationPlanner)

    def test_deterministic_pairs_still_satisfy_them(self) -> None:
        assert isinstance(DeterministicRouter(), IntentRouter)
        assert isinstance(RuleBasedPlanner(), MutationPlanner)

    def test_both_planners_are_structurally_interchangeable(self) -> None:
        """Same signature, different body. A planner that needed different arguments
        would have broken the wiring rather than swapped cleanly."""
        import inspect

        llm_params = inspect.signature(LLMPlanner.plan).parameters
        rule_params = inspect.signature(RuleBasedPlanner.plan).parameters
        assert set(llm_params) == set(rule_params)


class TestLLMRouter:
    async def test_reads_a_mutation_and_grounds_it(self) -> None:
        llm = llm_with(
            router_payload={
                "intent": "mutation",
                "confidence": 0.9,
                "rationale": "imperative capture verb",
                "instructions": [
                    {
                        "action": "create",
                        "quote": "add a task to send the invoice",
                        "title": "send the invoice",
                    }
                ],
            }
        )
        decision = await LLMRouter(llm).route(
            "add a task to send the invoice", today=TODAY
        )
        assert decision.intent is Intent.MUTATION
        assert decision.is_confident
        assert decision.slots["instruction_count"] == 1
        assert decision.slots["instructions"][0]["title"] == "send the invoice"

    async def test_splits_two_instructions(self) -> None:
        llm = llm_with(
            router_payload={
                "intent": "mutation",
                "confidence": 0.9,
                "instructions": [
                    {"action": "create", "quote": "add a task to send the invoice",
                     "title": "send the invoice"},
                    {"action": "complete", "quote": "mark the invoice done",
                     "target_ref": "the invoice"},
                ],
            }
        )
        decision = await LLMRouter(llm).route(
            "add a task to send the invoice and also mark the invoice done", today=TODAY
        )
        assert decision.slots["instruction_count"] == 2
        assert len(decision.slots["instructions"]) == 2

    async def test_an_instruction_the_user_never_typed_is_dropped(self) -> None:
        """The quote is checked against the utterance. A hallucinated instruction is not
        carried forward -- but the count still records it, so the gap gets reported."""
        llm = llm_with(
            router_payload={
                "intent": "mutation",
                "confidence": 0.95,
                "instructions": [
                    {"action": "create", "quote": "add a task to send the invoice",
                     "title": "send the invoice"},
                    {"action": "archive", "quote": "delete everything",
                     "target_ref": "all tasks"},
                ],
            }
        )
        decision = await LLMRouter(llm).route(
            "add a task to send the invoice", today=TODAY
        )
        instructions = decision.slots["instructions"]
        assert len(instructions) == 1
        assert instructions[0]["action"] == "create"
        # Counted before grounding, so the discrepancy is visible downstream.
        assert decision.slots["instruction_count"] == 2

    async def test_a_mutation_with_no_grounded_instruction_is_refused(self) -> None:
        """Confident intent, zero real instructions. Nothing to act on means no claim of
        being able to act."""
        llm = llm_with(
            router_payload={
                "intent": "mutation",
                "confidence": 0.99,
                "instructions": [
                    {"action": "archive", "quote": "burn the archive",
                     "target_ref": "all tasks"}
                ],
            }
        )
        decision = await LLMRouter(llm).route("add a task to send the invoice",
                                              today=TODAY)
        assert decision.intent is Intent.UNCLEAR
        assert not decision.is_confident

    async def test_confidence_outside_the_range_is_a_validation_failure(self) -> None:
        llm = llm_with(router_payload={"intent": "mutation", "confidence": 4.2,
                                       "instructions": []})
        router = LLMRouter(llm, fallback=DeterministicRouter())
        decision = await router.route("add a task to send the invoice", today=TODAY)
        # The schema rejected it, so the deterministic router answered instead.
        assert decision.intent is Intent.MUTATION
        assert decision.slots["title"] == "send the invoice"

    async def test_an_unavailable_provider_falls_back_rather_than_guessing(self) -> None:
        """No fixture registered for the purpose: the provider is effectively down. The
        result must still be a real answer, never a fabricated one."""
        router = LLMRouter(MockLLM(), fallback=DeterministicRouter())
        decision = await router.route("add a task to send the invoice", today=TODAY)
        assert decision.intent is Intent.MUTATION
        assert decision.slots["title"] == "send the invoice"

    async def test_with_no_fallback_a_dead_provider_refuses(self) -> None:
        decision = await LLMRouter(MockLLM()).route("add a task to send the invoice",
                                                    today=TODAY)
        assert not decision.is_confident


class TestLLMPlanner:
    async def _plan(self, payload, known_tasks, decision=None):
        llm = llm_with(plan_payload=payload)
        planner = LLMPlanner(llm)
        return await planner.plan(
            "do the thing",
            decision or _decision_from_instructions([{"action": "complete"}]),
            today=TODAY,
            known_tasks=known_tasks,
        )

    async def test_resolves_a_reference_against_real_tasks(self, known_tasks) -> None:
        proposals = await self._plan(
            {"changes": [{"kind": "complete_task", "instruction_index": 0,
                          "target_ref": "the q3 report", "confidence": 0.9}]},
            known_tasks,
        )
        assert len(proposals) == 1
        assert proposals[0].kind is ChangeKind.COMPLETE_TASK
        assert proposals[0].target_id == REPORT_ID

    async def test_a_model_supplied_target_id_is_ignored(self, known_tasks) -> None:
        """The model has no way to name a task. Even if it tries, the id that reaches the
        domain is the one this process resolved."""
        proposals = await self._plan(
            {"changes": [{"kind": "complete_task", "instruction_index": 0,
                          "target_ref": "the q3 report", "confidence": 0.9,
                          "target_id": str(OTHER_PROJECT)}]},
            known_tasks,
        )
        assert proposals[0].target_id == REPORT_ID

    async def test_an_unresolvable_reference_is_carried_forward_unresolved(
        self, known_tasks
    ) -> None:
        """Not dropped. A dropped proposal would shrink the plan and let the reply
        imply everything worked."""
        proposals = await self._plan(
            {"changes": [{"kind": "complete_task", "instruction_index": 0,
                          "target_ref": "the nonexistent thing", "confidence": 0.9}]},
            known_tasks,
        )
        assert len(proposals) == 1
        assert proposals[0].target_id is None
        assert proposals[0].target_ref == "the nonexistent thing"

    async def test_fields_outside_the_allowlist_are_discarded(self, known_tasks) -> None:
        """The model cannot redirect a task into another project, or smuggle a field the
        command does not accept."""
        proposals = await self._plan(
            {"changes": [{"kind": "create_task", "instruction_index": 0,
                          "confidence": 0.9,
                          "fields": {"title": "send the invoice",
                                     "project_id": str(OTHER_PROJECT)}}]},
            known_tasks,
        )
        assert proposals[0].fields == {"title": "send the invoice"}

    async def test_a_change_missing_a_required_field_produces_nothing(self, known_tasks) -> None:
        """Blocking needs a reason. Omitting the proposal is what lets the count gap
        surface as a clarification instead of a guess."""
        proposals = await self._plan(
            {"changes": [{"kind": "block_task", "instruction_index": 0,
                          "target_ref": "the q3 report", "confidence": 0.9,
                          "fields": {}}]},
            known_tasks,
        )
        assert proposals == ()

    async def test_an_instruction_index_past_the_end_is_dropped(self, known_tasks) -> None:
        proposals = await self._plan(
            {"changes": [{"kind": "complete_task", "instruction_index": 7,
                          "target_ref": "the q3 report", "confidence": 0.9}]},
            known_tasks,
            decision=_decision_from_instructions([{"action": "complete"}]),
        )
        assert proposals == ()

    async def test_two_changes_for_one_instruction_yield_one_proposal(
        self, known_tasks
    ) -> None:
        """A duplicate must not be applied twice -- completing an already-complete task
        would then fail for a reason the user never gave."""
        proposals = await self._plan(
            {"changes": [
                {"kind": "complete_task", "instruction_index": 0,
                 "target_ref": "the q3 report", "confidence": 0.9},
                {"kind": "complete_task", "instruction_index": 0,
                 "target_ref": "the grant draft", "confidence": 0.9},
            ]},
            known_tasks,
            decision=_decision_from_instructions([{"action": "complete"}]),
        )
        assert len(proposals) == 1
        assert proposals[0].target_id == REPORT_ID

    async def test_one_change_per_instruction_is_preserved(self, known_tasks) -> None:
        proposals = await self._plan(
            {"changes": [
                {"kind": "complete_task", "instruction_index": 0,
                 "target_ref": "the q3 report", "confidence": 0.9},
                {"kind": "archive_task", "instruction_index": 1,
                 "target_ref": "the grant draft", "confidence": 0.9},
            ]},
            known_tasks,
            decision=_decision_from_instructions(
                [{"action": "complete"}, {"action": "archive"}]
            ),
        )
        assert [p.kind for p in proposals] == [
            ChangeKind.COMPLETE_TASK, ChangeKind.ARCHIVE_TASK,
        ]

    async def test_a_dead_provider_yields_no_plan_and_so_a_clarification(
        self, known_tasks
    ) -> None:
        """No fixture: the planner cannot plan. Returning nothing turns into a question,
        which is the correct outcome -- not a guessed change."""
        planner = LLMPlanner(MockLLM())
        proposals = await planner.plan(
            "do the thing",
            _decision_from_instructions([{"action": "complete"}]),
            today=TODAY,
            known_tasks=known_tasks,
        )
        assert proposals == ()

    async def test_every_change_kind_survives_the_allowlist(self) -> None:
        """Each kind has a usable field set; none is stranded by its own validation."""
        from swayam.reasoning.mutation.llm_planner import ALLOWED_FIELDS, REQUIRED_FIELDS

        payloads = {
            ChangeKind.CREATE_TASK: {"title": "x"},
            ChangeKind.CREATE_EVENT: {
                "title": "x",
                "starts_at": "2026-10-10T10:00:00",
                "ends_at": "2026-10-10T11:00:00",
            },
            ChangeKind.SCHEDULE_TASK: {"due_date": "2026-10-02"},
            ChangeKind.COMPLETE_TASK: {},
            ChangeKind.REOPEN_TASK: {},
            ChangeKind.BLOCK_TASK: {"blocked_by": "legal"},
            ChangeKind.ARCHIVE_TASK: {},
            ChangeKind.RENAME_TASK: {"new_title": "y"},
            ChangeKind.ADD_NOTE: {"body": "z"},
        }
        for kind, required in REQUIRED_FIELDS.items():
            assert set(payloads[kind]) >= set(required), (
                f"{kind.value} cannot satisfy its own required fields"
            )
        assert set(ALLOWED_FIELDS) == set(ChangeKind)


def _decision_from_instructions(instructions: list[dict]):
    from swayam.reasoning.router.intent import IntentDecision

    return IntentDecision(
        Intent.MUTATION,
        0.9,
        {"instructions": instructions, "instruction_count": len(instructions)},
        "test",
    )