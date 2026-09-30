"""Mock-runtime scenarios.

Each scenario is a Phase 1 acceptance test written as a transcript: the turn sequence,
what must be true afterwards, and what the system is forbidden to do wrong. They run
against the real domain, storage and reasoning code with only the model faked, so a
passing scenario means the pipeline works -- not that a prompt happened to land.

Scenarios marked ``pending_milestone`` exercise paths whose projections do not exist
yet. They are kept here, and asserted to decline, so that turning one on later is a
matter of filling in the expectation rather than discovering the gap.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

Expectation = Literal[
    "captured", "rescheduled", "completed", "reopened", "blocked", "archived",
    "renamed", "noted", "clarified", "declined", "partial", "any",
]


@dataclass(frozen=True, slots=True)
class Turn:
    utterance: str
    expect: Expectation
    note: str = ""


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    description: str
    turns: Sequence[Turn]
    pending_milestone: str | None = None
    tags: tuple[str, ...] = field(default=())


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="capture_weekly_tasks",
        description="Natural language becomes correct events, and only correct events.",
        tags=("mutation", "m2"),
        turns=(
            Turn("add a task to send the Q3 report", "captured"),
            Turn("add a task to review the budget due friday", "captured"),
            Turn("add a task to email the client", "captured"),
        ),
    ),
    Scenario(
        name="move_deadline",
        description="A reschedule emits a move, not an overwrite, and says what changed.",
        tags=("mutation", "m2"),
        turns=(
            Turn("add a task to send the grant draft", "captured"),
            Turn("move the grant draft to thursday", "rescheduled"),
        ),
    ),
    Scenario(
        name="complete_a_task",
        description="Completion transitions the aggregate and confirms plainly.",
        tags=("mutation", "m2"),
        turns=(
            Turn("add a task to send the Q3 report", "captured"),
            Turn("mark the q3 report done", "completed"),
        ),
    ),
    Scenario(
        name="ambiguous_reference",
        description="An unresolvable reference must ask, never guess. Both tasks match "
        "'the report', so there is no correct answer to pick.",
        tags=("safety", "m2"),
        turns=(
            Turn("add a task to send the Q3 report", "captured"),
            Turn("add a task to review the Q3 report", "captured"),
            Turn("mark the report done", "clarified"),
        ),
    ),
    Scenario(
        name="unclear_utterance",
        description="Low-confidence routing declines instead of inventing a change.",
        tags=("safety", "m2"),
        turns=(
            Turn("fix it", "clarified"),
            Turn("yeah do the thing", "clarified"),
        ),
    ),
    Scenario(
        name="task_lifecycle",
        description="Every change kind the domain supports is reachable by conversation: "
        "rename, note, block, reschedule, archive.",
        tags=("mutation", "m2"),
        turns=(
            Turn("add a task to send the q3 report", "captured"),
            Turn("rename the q3 report to the board deck", "renamed"),
            Turn("add a note to the board deck that legal signed off", "noted"),
            Turn("block the board deck because legal is slow", "blocked"),
            Turn("move the board deck to friday", "rescheduled"),
            Turn("archive the board deck", "archived"),
        ),
    ),
    Scenario(
        name="reopen_a_task",
        description="Completion is not a one-way door.",
        tags=("mutation", "m2"),
        turns=(
            Turn("add a task to send the q3 report", "captured"),
            Turn("mark the q3 report done", "completed"),
            Turn("reopen the q3 report", "reopened"),
        ),
    ),
    Scenario(
        name="two_instructions_one_turn",
        description="Two requests in one sentence produce two changes and two "
        "confirmations, not one change and a vague success.",
        tags=("mutation", "m2"),
        turns=(
            Turn("add a task to send the invoice, add a task to book the venue", "captured"),
            Turn("add a task to send the pdf and also move the grant draft to friday",
                 "captured"),
        ),
    ),
    Scenario(
        name="partial_execution_is_reported",
        description="The regression this scenario exists for. One instruction succeeds and "
        "one does not, and the reply says so. A reply that reported only the success "
        "would leave the user believing both had happened.",
        tags=("safety", "m2"),
        turns=(
            Turn("add a task to send the q3 report", "captured"),
            Turn("mark the q3 report done and also mark the nonexistent thing done",
                 "partial"),
        ),
    ),
    Scenario(
        name="a_conjunction_in_a_title_is_not_a_split",
        description="'the research and interviews' is one title. Treating the 'and' as a "
        "separator would silently discard half of it.",
        tags=("safety", "m2"),
        turns=(
            Turn("add a task to write the research and interviews", "captured"),
        ),
    ),
    Scenario(
        name="am_i_overcommitted",
        description="Load arithmetic. Needs commitment_load_week -- M3.",
        pending_milestone="M3",
        tags=("query",),
        turns=(Turn("am I overcommitted this month", "declined"),),
    ),
    Scenario(
        name="how_did_last_week_go",
        description="Cross-domain synthesis with citations. Needs the deep path -- M4.",
        pending_milestone="M4",
        tags=("query",),
        turns=(Turn("how did last week go", "declined"),),
    ),
    Scenario(
        name="stale_claim",
        description="A March claim reconciled against today's state. Needs "
        "conversational_claim -- M3.",
        pending_milestone="M3",
        tags=("memory",),
        turns=(Turn("what did I say I'd do this week", "declined"),),
    ),
)


def get_scenario(name: str) -> Scenario:
    for scenario in SCENARIOS:
        if scenario.name == name:
            return scenario
    raise KeyError(f"unknown scenario: {name}")


def scenario_names() -> list[str]:
    return [s.name for s in SCENARIOS]
