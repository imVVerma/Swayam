"""Eval gate — runs each fixture against the LLM-backed pipeline.

This gate exercises ``build_runtime(reasoning="llm")`` with a ``MockLLM`` whose
fixtures return the expected ``RouteOutput`` / ``PlanOutput`` for the case.
It is the *only* place that exercises the LLM-backed reasoning path; the
deterministic keyword router is deliberately not invoked, because that would
test code already covered by the existing unit test suite under a different
name.

Each fixture's ``expect`` is assert-equal against the observed ``Observation``.
The mock lane must score exactly 1.0 (every assertion passes).  Deviations are
harness bugs, not "tolerated gaps".
"""

from __future__ import annotations

import pytest

from swayam.domain.task import (
    AddNote,
    ChangeStatus,
    ScheduleTask,
)
from swayam.llm.mock import MockLLM
from swayam.runtime import build_runtime
from tests.evals.classify import observe, observed_forbidden
from tests.evals.spec import Case


def _register_fixtures(ml: MockLLM, case: Case) -> None:
    """Register MockLLM completions that produce the case's expected behaviour.

    The registered completions are only the *router* and *planner* answers;
    the orchestrator's ``_why_no_proposal`` / ``_apply`` then determine the
    final reply and committed events.
    """

    # ----- router.classify -----
    is_clarified = case.expect.outcome == "clarified"
    route_fixture: dict = case.route_fixture or {
        "intent": "mutation" if is_clarified else case.expect.outcome,
        "confidence": 0.4 if is_clarified else 1.0,
        "instructions": [],
    }
    ml.register("router.classify", route_fixture)

    # ----- mutation.plan -----
    plan_fixture: dict = case.plan_fixture or {
        "changes": [],
    }
    ml.register("mutation.plan", plan_fixture)


async def _build_llm_runtime(case: Case, *, llm: MockLLM) -> object:
    """Build a runtime whose ``llm`` is the given adapter, reasoning='llm'."""
    _register_fixtures(llm, case)

    # Use a frozen clock pinned to the fixture's "today" for deterministic tests.
    from datetime import datetime

    from swayam.core.clock import FrozenClock
    today = datetime.fromisoformat(case.today + "T00:00:00+00:00")
    clock = FrozenClock(today)

    runtime = await build_runtime(
        clock=clock,
        user_id="owner",
        reasoning="llm",
        llm=llm,  # type: ignore[arg-type] - build_runtime accepts LLM protocol
    )
    return runtime


async def _observe_case(case: Case, runtime: object) -> object:
    """Run the fixture's turn through the runtime and return the Observation."""

    # Seed storage with the fixture's seed tasks.
    if case.seed_tasks:
        from datetime import date

        from swayam.core.ids import CorrelationId
        from swayam.domain.task import CreateTask, TaskStatus
        from swayam.events.envelope import Actor
        from swayam.observability.trace import Trace
        from swayam.storage.repositories import DEFAULT_PROJECT_ID, TaskRepository

        actor = Actor.user("owner")
        correlation_id = CorrelationId(Trace().correlation_id)
        task_repo = TaskRepository(runtime.storage.events, runtime.clock)

        for seed in case.seed_tasks:
            task = await task_repo.create(
                CreateTask(title=seed.title, project_id=DEFAULT_PROJECT_ID),
                actor=actor,
                correlation_id=correlation_id,
                causation_id=correlation_id,
                idempotency_key=f"seed:{seed.title}",
            )
            if seed.due:
                due_date = date.fromisoformat(seed.due)
                await task_repo.execute(
                    task.task_id,
                    ScheduleTask(due_date=due_date),
                    actor=actor,
                    correlation_id=correlation_id,
                    causation_id=correlation_id,
                )
            if seed.status == "complete":
                await task_repo.execute(
                    task.task_id,
                    ChangeStatus(to_status=TaskStatus.COMPLETE),
                    actor=actor,
                    correlation_id=correlation_id,
                    causation_id=correlation_id,
                )
            for note in seed.notes:
                await task_repo.execute(
                    task.task_id,
                    AddNote(body=note),
                    actor=actor,
                    correlation_id=correlation_id,
                    causation_id=correlation_id,
                )

            # Replay events into projection
            events = await runtime.storage.events.read_stream(task.task_id, from_seq=0)
            for recorded in events:
                runtime.projection.apply(recorded)

    turn_result = await runtime.orchestrator.handle(case.turn)

    task_titles = tuple(t.title for t in runtime.projection.all_tasks() if t.exists)
    obs = observe(turn_result, task_titles=task_titles)
    return obs


@pytest.mark.eval
@pytest.mark.asyncio
@pytest.mark.parametrize("case_name", [
    "mutation_positive", "ambiguous_reference",
    "missing_required_field", "malformed_date",
    "overconfidence_trap",
    "dd_mm_yyyy_format", "dd_mm_format", "ordinal_date",
    "next_week", "end_of_month",
    "spoken_time", "time_range",
    "dictation_paragraph",
    "create_task_with_due_date", "create_task_due_tomorrow",
    "complete_existing_task", "reschedule_task",
    "ambiguous_reference_multiple", "ambiguous_reference_none",
    "missing_due_date", "missing_blocker",
    "malformed_date_relative", "malformed_date_weekday",
    "overconfidence_delete", "overconfidence_rename",
    "create_task_with_time", "create_event_with_time_range",
    "create_event_appointment", "create_event_birthday",
    "create_event_conference", "create_task_with_time",
    "time_parsing_10pm", "time_day_parsing",
    "multi_instruction_compound",
])
async def test_eval_mock_lane(case_name: str, request) -> None:
    """Mock lane — must score exactly 1.0.

    This is the contract test: the deterministic (MockLLM) path must satisfy
    every fixture's expectations.  If this fails, the harness itself is broken,
    not the model.
    """
    from tests.evals.fixtures import load_fixture

    fixture_data = load_fixture(case_name)
    case = Case.model_validate(fixture_data)

    ml = MockLLM(trace=None)
    _register_fixtures(ml, case)

    runtime = await _build_llm_runtime(case, llm=ml)

    obs = await _observe_case(case, runtime)

    # Assert every field of the Expectation matches the observation.
    assert obs.outcome == case.expect.outcome, (
        f"outcome: expected {case.expect.outcome}, got {obs.outcome}"
    )
    assert obs.commits == case.expect.commits, (
        f"commits: expected {case.expect.commits}, got {obs.commits}"
    )
    assert obs.clarifies_about == case.expect.clarifies_about, (
        f"clarifies_about: expected {case.expect.clarifies_about}, got {obs.clarifies_about}"
    )
    if case.expect.required_field is not None:
        assert obs.required_field == case.expect.required_field, (
            f"required_field: expected {case.expect.required_field}, got {obs.required_field}"
        )

    # must_not_do: none of the forbidden behaviours may be present.
    forbid = observed_forbidden(obs)
    for f in case.expect.must_not_do:
        assert f not in forbid, (
            f"must_not_do: forbidden '{f}' was observed"
        )