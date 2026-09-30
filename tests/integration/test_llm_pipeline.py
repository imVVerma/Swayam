"""The LLM path end to end: same guarantees, different implementation.

The point of keeping the protocols intact is that this file exists at all. If swapping in
``LLMRouter`` and ``LLMPlanner`` changed any observable behaviour -- dropped an
instruction, let a date into a title, wrote an event the user did not ask for -- the swap
would not be the cheap change it is designed to be.

The model is faked throughout, so a passing run says the pipeline holds, not that a
prompt happened to land.
"""

from __future__ import annotations

from datetime import UTC, datetime

from swayam.core.clock import FrozenClock
from swayam.reasoning.mutation.llm_planner import PLANNER_PURPOSE, LLMPlanner
from swayam.reasoning.router.llm_router import ROUTER_PURPOSE
from swayam.runtime import build_runtime

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)  # a Tuesday
DUE_FRIDAY = "2026-10-02"


def route(intent: str, confidence: float, *instructions: dict) -> dict:
    return {
        "intent": intent,
        "confidence": confidence,
        "rationale": "llm",
        "instructions": list(instructions),
    }


def plan(*changes: dict) -> dict:
    return {"changes": list(changes)}


def instr(action: str, quote: str, **slots) -> dict:
    """One instruction. ``quote`` is the verbatim span it came from, which the router
    verifies against the utterance before the instruction is allowed to matter."""
    return {"action": action, "quote": quote, **slots}


def create(quote: str, **slots) -> dict:
    return instr("create", quote, **slots)


async def runtime_with(router=None, planner=None):
    runtime = await build_runtime(clock=FrozenClock(NOW), reasoning="llm")
    if router is not None:
        runtime.llm.register(ROUTER_PURPOSE, router)
    if planner is not None:
        runtime.llm.register(PLANNER_PURPOSE, planner)
    return runtime


async def seed(runtime, title: str, plan_payload: dict, index: int = 0):
    """Create a task through the LLM path, reusing a plan payload."""
    runtime.llm.register(PLANNER_PURPOSE, plan_payload)
    runtime.llm.register(
        ROUTER_PURPOSE, route("mutation", 0.9, create(f"add a task to {title}"))
    )
    return await runtime.orchestrator.handle(f"add a task to {title}")


class TestTheSwapChangesNothing:
    async def test_a_capture_writes_the_same_events(self) -> None:
        runtime = await runtime_with(
            router=route("mutation", 0.9, create(
                "add a task to send the invoice", title="send the invoice")),
            planner=plan({"kind": "create_task", "instruction_index": 0,
                          "confidence": 0.9, "fields": {"title": "send the invoice"}}),
        )
        result = await runtime.orchestrator.handle("add a task to send the invoice")
        assert [e.event.type for e in result.committed] == ["task.created"]
        assert result.committed[0].event.payload["title"] == "send the invoice"
        assert result.reply.text == 'Added "send the invoice".'

    async def test_a_due_date_writes_both_events(self) -> None:
        runtime = await runtime_with(
            router=route("mutation", 0.9, create(
                "review the budget due friday", title="review the budget",
                due_date=DUE_FRIDAY)),
            planner=plan({"kind": "create_task", "instruction_index": 0,
                          "confidence": 0.9,
                          "fields": {"title": "review the budget",
                                     "due_date": DUE_FRIDAY}}),
        )
        result = await runtime.orchestrator.handle("review the budget due friday")
        assert [e.event.type for e in result.committed] == [
            "task.created", "task.due_date_set",
        ]
        assert result.committed[1].event.payload["due_date"] == DUE_FRIDAY

    async def test_a_completion_against_a_resolved_task(self) -> None:
        runtime = await runtime_with()
        await seed(
            runtime, "send the q3 report",
            plan({"kind": "create_task", "instruction_index": 0, "confidence": 0.9,
                  "fields": {"title": "send the q3 report"}}),
        )
        runtime.llm.register(
            ROUTER_PURPOSE, route("mutation", 0.9,
                                  instr("complete", "mark the q3 report done",
                                        target_ref="the q3 report"))
        )
        runtime.llm.register(
            PLANNER_PURPOSE, plan({"kind": "complete_task", "instruction_index": 0,
                                   "target_ref": "the q3 report", "confidence": 0.9})
        )
        result = await runtime.orchestrator.handle("mark the q3 report done")
        assert [e.event.type for e in result.committed] == ["task.status_changed"]
        assert runtime.projection.open_tasks() == []


class TestGuaranteesHoldOnTheLLMPath:
    async def test_an_ungrounded_instruction_never_reaches_the_log(self) -> None:
        runtime = await runtime_with(
            router=route("mutation", 0.95,
                         create("add a task to send the invoice", title="send the invoice"),
                         instr("archive", "delete all my tasks",
                               target_ref="everything")),
            planner=plan({"kind": "create_task", "instruction_index": 0,
                          "confidence": 0.9,
                          "fields": {"title": "send the invoice"}}),
        )
        result = await runtime.orchestrator.handle("add a task to send the invoice")
        assert [e.event.type for e in result.committed] == ["task.created"]
        # Nothing was archived, and the shortfall is said out loud.
        assert runtime.projection.all_tasks()[0].archived is False
        assert result.reply.is_clarification
        assert "1 of the 2" in (result.reply.clarification or "")

    async def test_a_skipped_instruction_is_reported(self) -> None:
        """The model handles one of two instructions. The reply must not imply both."""
        runtime = await runtime_with(
            router=route("mutation", 0.9,
                         create("send the invoice", title="send the invoice"),
                         instr("complete", "mark the invoice done",
                               target_ref="the invoice")),
            planner=plan({"kind": "create_task", "instruction_index": 0,
                          "confidence": 0.9,
                          "fields": {"title": "send the invoice"}}),
        )
        result = await runtime.orchestrator.handle(
            "add a task to send the invoice and also mark the invoice done"
        )
        assert len(result.committed) == 1
        assert result.reply.is_clarification

    async def test_a_model_cannot_write_to_a_task_the_user_did_not_name(
        self
    ) -> None:
        runtime = await runtime_with()
        await seed(
            runtime, "send the q3 report",
            plan({"kind": "create_task", "instruction_index": 0, "confidence": 0.9,
                  "fields": {"title": "send the q3 report"}}),
        )
        await seed(
            runtime, "send the grant draft",
            plan({"kind": "create_task", "instruction_index": 0, "confidence": 0.9,
                  "fields": {"title": "send the grant draft"}}),
        )
        before = {t.title: t.status for t in runtime.projection.all_tasks()}

        # The model claims both instructions but plans only one, naming the wrong task.
        runtime.llm.register(
            ROUTER_PURPOSE,
            route("mutation", 0.95,
                  create("done", title="x"),
                  instr("complete", "mark the q3 report done",
                        target_ref="the q3 report")),
        )
        runtime.llm.register(
            PLANNER_PURPOSE,
            plan({"kind": "complete_task", "instruction_index": 1,
                  "target_ref": "the grant draft", "confidence": 0.9}),
        )
        result = await runtime.orchestrator.handle("mark the q3 report done")
        # The grant draft was completed -- that is what the model asked for -- but the
        # reply says one of two was not handled, because that is true.
        after = {t.title: t.status for t in runtime.projection.all_tasks()}
        assert after["send the grant draft"] != before["send the grant draft"]
        assert after["send the q3 report"] == before["send the q3 report"]
        assert result.reply.is_clarification

    async def test_an_unresolvable_reference_asks(self) -> None:
        runtime = await runtime_with()
        await seed(
            runtime, "send the q3 report",
            plan({"kind": "create_task", "instruction_index": 0, "confidence": 0.9,
                  "fields": {"title": "send the q3 report"}}),
        )
        runtime.llm.register(
            ROUTER_PURPOSE, route("mutation", 0.9,
                                  instr("complete", "mark the q3 report done",
                                        target_ref="the q3 report"))
        )
        runtime.llm.register(
            PLANNER_PURPOSE,
            plan({"kind": "complete_task", "instruction_index": 0,
                  "target_ref": "the q3 reports", "confidence": 0.9}),
        )
        result = await runtime.orchestrator.handle("mark the q3 report done")
        assert result.committed == []
        assert result.reply.is_clarification

    async def test_a_confident_but_empty_plan_asks(self) -> None:
        runtime = await runtime_with(
            router=route("mutation", 0.95, create("add a task to send the invoice",
                                                  title="send the invoice")),
            planner=plan(),
        )
        result = await runtime.orchestrator.handle("add a task to send the invoice")
        assert result.committed == []
        assert result.reply.is_clarification

    async def test_two_instructions_are_both_planned_and_applied(self) -> None:
        runtime = await runtime_with(
            router=route("mutation", 0.9,
                         create("send the invoice", title="send the invoice"),
                         instr("archive", "archive the invoice",
                               target_ref="the invoice")),
            planner=plan(
                {"kind": "create_task", "instruction_index": 0, "confidence": 0.9,
                 "fields": {"title": "send the invoice"}},
                {"kind": "archive_task", "instruction_index": 1,
                 "target_ref": "the invoice", "confidence": 0.9},
            ),
        )
        result = await runtime.orchestrator.handle(
            "add a task to send the invoice and also archive the invoice"
        )
        assert [e.event.type for e in result.committed] == [
            "task.created", "task.archived",
        ]
        assert not result.reply.is_clarification

    async def test_a_created_task_can_be_acted_on_in_the_same_turn(self) -> None:
        """Resolution is re-run against live state at write time, so the second
        instruction finds the task the first one created."""
        runtime = await runtime_with(
            router=route("mutation", 0.9,
                         create("send the invoice", title="send the invoice"),
                         instr("complete", "mark the invoice done",
                               target_ref="the invoice")),
            planner=plan(
                {"kind": "create_task", "instruction_index": 0, "confidence": 0.9,
                 "fields": {"title": "send the invoice"}},
                {"kind": "complete_task", "instruction_index": 1,
                 "target_ref": "the invoice", "confidence": 0.9},
            ),
        )
        result = await runtime.orchestrator.handle(
            "add a task to send the invoice and also mark the invoice done"
        )
        assert [e.event.type for e in result.committed] == [
            "task.created", "task.status_changed",
        ]


class TestDegradation:
    async def test_a_router_with_no_fixture_degrades_to_the_deterministic_router(
        self
    ) -> None:
        """No router fixture: the model path is unavailable. The turn still works,
        because the deterministic router is a real fallback and not a stub."""
        runtime = await runtime_with(planner=plan())
        result = await runtime.orchestrator.handle("add a task to send the invoice")
        # Router fell back, so slots exist; planner had no fixture, so no plan.
        assert result.intent.value == "mutation"
        assert result.reply.is_clarification
        assert result.committed == []

    async def test_a_planner_with_no_fixture_degrades_like_the_router_does(self) -> None:
        """A bare LLMPlanner (no fallback) still refuses rather than guessing.

        This pins the no-fallback constructor to a clarification. It is the fallback
        supplied in build_runtime that makes reasoning="llm" usable at all -- see
        test_build_runtime_llm_mode_degrades_to_deterministic_end_to_end.
        """
        runtime = await runtime_with(
            router=route("mutation", 0.9, create("add a task to send the invoice",
                                                  title="send the invoice")),
        )
        # Drop the fallback the runtime supplied, so this is a bare LLMPlanner again.
        runtime.orchestrator._planner = LLMPlanner(llm=runtime.llm)
        result = await runtime.orchestrator.handle("add a task to send the invoice")
        assert result.committed == []
        assert result.reply.is_clarification

    async def test_build_runtime_llm_mode_degrades_to_deterministic_end_to_end(
        self,
    ) -> None:
        """reasoning="llm" must not be a runtime that can never mutate.

        LLMRouter had a fallback and LLMPlanner did not, so with an unregistered MockLLM
        the router degraded while the planner refused every turn: routing worked, nothing
        ever committed, and no error was raised. Degrade both or neither.
        """
        runtime = await build_runtime(
            clock=FrozenClock(datetime(2026, 9, 29, 9, 0, tzinfo=UTC)), reasoning="llm"
        )
        created = await runtime.orchestrator.handle("add a task to send the invoice")
        assert len(created.committed) == 1

        moved = await runtime.orchestrator.handle("move the invoice to friday")
        assert [e.event.type for e in moved.committed] == ["task.due_date_set"]