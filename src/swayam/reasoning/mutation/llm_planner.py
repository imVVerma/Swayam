"""LLM-backed mutation planner.

Implements the same ``MutationPlanner`` protocol as ``RuleBasedPlanner``. Where the
deterministic planner maps verbs to change kinds, this one reads the sentence and picks
the kinds -- and then has its output laundered through three checks before a single
proposal exists.

The laundering is the point. A model asked to emit structured changes will, sooner or
later, emit one that references a task that does not exist, an id it made up, or a field
the command does not accept. Each of those is caught here, in code:

1. **Grounding.** A change carries the index of the instruction it came from. Indices
   outside the instruction list are dropped, and the orchestrator's instruction count
   comparison reports the gap. There is no way to quietly handle two of three
   instructions.
2. **Reference resolution.** ``target_id`` is never read from the model. The model
   supplies the user's own words, and *this* layer resolves them against real tasks,
   with the same resolver the deterministic planner uses. An id the model invented is
   structurally impossible to express.
3. **Field allowlist.** Each ``ChangeKind`` accepts a fixed set of fields. Anything else
   the model produced is discarded rather than forwarded into a domain command.

None of this makes the model more trusted. It makes the model unable to cause a change
the user did not ask for, which is the property Phase 1 is actually buying.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from swayam.core.errors import SwayamError
from swayam.domain.task import Task
from swayam.llm.port import LLM
from swayam.reasoning.mutation.proposal import (
    REQUIRED_FIELDS as _REQUIRED_FIELDS,
)
from swayam.reasoning.mutation.proposal import ChangeKind, ProposedChange
from swayam.reasoning.mutation.resolve import resolve_task
from swayam.reasoning.mutation.rule_planner import MutationPlanner
from swayam.reasoning.router.intent import Intent, IntentDecision

PLANNER_PURPOSE = "mutation.plan"

# The only fields each command may be built from. Anything else the model produced is
# dropped: an unrecognised key is either noise or an attempt to reach past the command.
ALLOWED_FIELDS: dict[ChangeKind, frozenset[str]] = {
    ChangeKind.CREATE_TASK: frozenset({"title", "due_date"}),
    ChangeKind.CREATE_EVENT: frozenset(
        {"title", "starts_at", "ends_at", "kind", "location", "notes", "all_day"}
    ),
    ChangeKind.SCHEDULE_TASK: frozenset({"due_date"}),
    ChangeKind.COMPLETE_TASK: frozenset(),
    ChangeKind.REOPEN_TASK: frozenset(),
    ChangeKind.BLOCK_TASK: frozenset({"blocked_by"}),
    ChangeKind.ARCHIVE_TASK: frozenset({"reason"}),
    ChangeKind.RENAME_TASK: frozenset({"new_title"}),
    ChangeKind.ADD_NOTE: frozenset({"body"}),
}

REQUIRED_FIELDS = _REQUIRED_FIELDS
"""Re-exported from proposal.py, where it is authoritative."""

# Kinds that act on an existing task and therefore need a resolvable reference.
create_kinds = {ChangeKind.CREATE_TASK, ChangeKind.CREATE_EVENT}
NEEDS_TARGET: frozenset[ChangeKind] = frozenset(ALLOWED_FIELDS) - create_kinds

PLANNER_PROMPT = """\
Turn each instruction into at most one proposed change.

Rules:
- Emit exactly one change per instruction you can act on, and set "instruction_index"
  to that instruction's position.
- If an instruction cannot be turned into a change, emit nothing for it. Do not
  substitute a different change.
- "target_ref" is the user's own words for the task. Never invent an id; the system
  resolves the reference itself.
- Use only the fields listed for the change kind. Leave optional fields out entirely
  rather than guessing a value.
"""


class ProposedChangePayload(BaseModel):
    """The model's proposal, before it has been checked against anything."""

    kind: ChangeKind
    instruction_index: int = Field(ge=0)
    target_ref: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    rationale: str = ""


class PlanOutput(BaseModel):
    changes: list[ProposedChangePayload] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LLMPlanner:
    llm: LLM
    fallback: MutationPlanner | None = None

    async def plan(
        self,
        utterance: str,
        decision: IntentDecision,
        *,
        today: date,
        known_tasks: Sequence[Task] = (),
    ) -> tuple[ProposedChange, ...]:
        if decision.intent not in (Intent.MUTATION, Intent.CAPTURE):
            return ()

        instructions = decision.slots.get("instructions")
        if not isinstance(instructions, list):
            instructions = []

        try:
            output = await self.llm.complete(
                purpose=PLANNER_PURPOSE,
                instruction=PLANNER_PROMPT,
                payload={
                    "utterance": utterance,
                    "today": today.isoformat(),
                    "instructions": instructions,
                    "known_tasks": [
                        {"task_id": str(t.task_id), "title": t.title, "status": t.status.value}
                        for t in known_tasks
                        if t.exists
                    ],
                },
                schema=PlanOutput,
            )
        except (LookupError, SwayamError):
            # Same reasoning as LLMRouter's fallback: the router degrades to the
            # deterministic path when the model is unavailable, and a router that routed
            # while a planner that refused left the pair inconsistent -- reasoning="llm"
            # could never mutate anything, silently. Degrade both or neither.
            if self.fallback is not None:
                return await self.fallback.plan(
                    utterance, decision, today=today, known_tasks=known_tasks
                )
            return ()

        proposals: list[ProposedChange] = []
        seen: set[int] = set()
        for payload in output.changes:
            index = payload.instruction_index
            if index >= len(instructions) or index in seen:
                # Either an instruction that does not exist or a duplicate of one already
                # planned. Dropping it shrinks the plan, and the orchestrator reports the
                # resulting count gap rather than treating the turn as fully handled.
                continue
            seen.add(index)
            proposal = _build(payload, known_tasks)
            if proposal is not None:
                proposals.append(proposal)
        return tuple(proposals)


def _build(payload: ProposedChangePayload, known_tasks: Sequence[Task]) -> ProposedChange | None:
    kind = payload.kind
    allowed = ALLOWED_FIELDS.get(kind, frozenset[str]())
    fields = {key: value for key, value in payload.fields.items() if key in allowed}
    for required in REQUIRED_FIELDS.get(kind, ()):
        if not str(fields.get(required, "")).strip():
            # A required field the model left out means the proposal is incomplete.
            # Omitting it lets the count gap surface as a clarification; guessing the
            # field would put a value in the log the user never chose.
            return None

    target_ref = (payload.target_ref or "").strip() or None
    target_id = None
    if kind in NEEDS_TARGET:
        if not target_ref:
            return None
        resolved = resolve_task(target_ref, known_tasks)
        # Unresolved is carried forward on purpose. The proposal reaches _apply, which
        # refuses it, and the user learns which of their instructions did not land.
        target_id = resolved.task_id if resolved is not None else None
    elif target_ref:
        resolved = resolve_task(target_ref, known_tasks)
        if resolved is not None:
            target_ref, target_id = target_ref, resolved.task_id

    return ProposedChange(
        kind=kind,
        fields=fields,
        target_ref=target_ref,
        target_id=target_id,
        confidence=payload.confidence,
        rationale=payload.rationale or "llm plan",
    )