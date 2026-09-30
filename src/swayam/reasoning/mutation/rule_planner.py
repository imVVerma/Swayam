"""Rule-based mutation planner.

Deterministic counterpart to ``LLMPlanner``, implementing the same protocol. It is
deliberately narrow: it handles the utterance shapes in ``evals/fixtures/`` and declines
everything else, which the orchestrator turns into a clarification.

The narrowness is the point. A planner may be more capable later but never more
*constrained*: whatever ships clears the same ``ProposedChange -> domain validation ->
event`` path, and the same eval gates.

Two rules govern what leaves this module, and both exist because of a real failure mode:

- **One proposal per instruction, and no more.** The router reports how many
  instructions it found; the orchestrator compares that against how many proposals came
  back. A planner that quietly handles two of three instructions cannot hide behind the
  count.
- **An unresolvable reference is proposed anyway, with no target id.** Dropping it would
  shrink the plan and read as "everything went fine". Carrying it unresolved means the
  orchestrator reaches ``_apply``, which refuses, and the user is told which part failed
  and which parts succeeded.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from swayam.domain.task import Task
from swayam.reasoning.mutation.proposal import ChangeKind, ProposedChange
from swayam.reasoning.mutation.resolve import resolve_task
from swayam.reasoning.router.intent import Intent, IntentDecision


@runtime_checkable
class MutationPlanner(Protocol):
    async def plan(
        self,
        utterance: str,
        decision: IntentDecision,
        *,
        today: date,
        known_tasks: Sequence[Task] = (),
    ) -> tuple[ProposedChange, ...]: ...


@dataclass(frozen=True, slots=True)
class RuleBasedPlanner:
    """Stateless. Candidate tasks arrive per call from the projection, so the planner
    holds no view of the world that could go stale."""

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
        if not isinstance(instructions, list) or not instructions:
            # A decision that never went through clause-aware routing still has to be
            # honoured, so fall back to the promoted top-level slots.
            instructions = [
                {k: v for k, v in decision.slots.items() if k != "instructions"}
            ]

        proposals: list[ProposedChange] = []
        for instruction in instructions:
            if not isinstance(instruction, dict):
                continue
            proposal = _propose(instruction, known_tasks)
            if proposal is not None:
                proposals.append(proposal)
        return tuple(proposals)


def _propose(
    instruction: dict[str, object], known_tasks: Sequence[Task]
) -> ProposedChange | None:
    # No fallback to "update" here either. The router sends action=None when a bare capture
    # verb carried the whole sentence ("book train tickets due october 19"); defaulting to
    # "update" turned those into attempts to reschedule a task nobody named.
    action = str(instruction["action"]) if instruction.get("action") else ""
    due_raw = instruction.get("due_date")
    due = date.fromisoformat(due_raw) if isinstance(due_raw, str) else None

    if action in ("create", "") and instruction.get("title"):
        # The router only emits action=None alongside a title; an actionless instruction
        # with no title is nothing to act on.
        action = "create"

    if action == "create":
        # The title is the user's own words, only trimmed. ``clean_title`` is for
        # *matching* an existing reference; applying it here would silently strip
        # articles out of a title the user chose.
        title = str(instruction.get("title") or "").strip()
        if not title:
            return None
        fields: dict[str, object] = {"title": title}
        if due is not None:
            fields["due_date"] = due.isoformat()
        return ProposedChange(
            kind=ChangeKind.CREATE_TASK,
            fields=fields,
            confidence=0.85,
            rationale="explicit capture verb with an extractable title",
        )

    target = str(instruction.get("target_ref") or "").strip()
    if not target:
        return None
    resolved = resolve_task(target, known_tasks)

    if action == "complete":
        return _targeted(
            ChangeKind.COMPLETE_TASK,
            target,
            resolved,
            confidence=0.9,
            rationale="completion verb against a uniquely matched open task",
        )
    if action == "reopen":
        return _targeted(
            ChangeKind.REOPEN_TASK,
            target,
            resolved,
            confidence=0.85,
            rationale="reopen verb against a uniquely matched task",
        )
    if action == "archive":
        # "delete" routes here too. Archive is the reversible version of the same
        # intent, and Phase 1 has no hard delete.
        return _targeted(
            ChangeKind.ARCHIVE_TASK,
            target,
            resolved,
            confidence=0.8,
            rationale="archive verb against a uniquely matched task",
        )
    if action == "reschedule":
        if due is None:
            # No date, so no change to propose. Omitting the proposal is deliberate: the
            # instruction gap surfaces as a clarification instead of a silent no-op.
            return None
        return _targeted(
            ChangeKind.SCHEDULE_TASK,
            target,
            resolved,
            fields={"due_date": due.isoformat()},
            confidence=0.85,
            rationale="explicit date in a reschedule request",
        )
    if action == "block":
        blocked_by = str(instruction.get("blocked_by") or "").strip()
        if not blocked_by:
            return None
        return _targeted(
            ChangeKind.BLOCK_TASK,
            target,
            resolved,
            fields={"blocked_by": blocked_by},
            confidence=0.85,
            rationale="explicit blocker given with the block request",
        )
    if action == "rename":
        new_title = str(instruction.get("new_title") or "").strip()
        if not new_title:
            return None
        return _targeted(
            ChangeKind.RENAME_TASK,
            target,
            resolved,
            fields={"new_title": new_title},
            confidence=0.85,
            rationale="explicit new title given with the rename request",
        )
    if action == "note":
        body = str(instruction.get("body") or "").strip()
        if not body:
            return None
        return _targeted(
            ChangeKind.ADD_NOTE,
            target,
            resolved,
            fields={"body": body},
            confidence=0.8,
            rationale="note body with a resolvable target",
        )

    # 'update' and 'prioritise' have no ChangeKind. Emitting nothing here is what makes
    # the instruction gap visible in the reply rather than a shrug.
    return None


def _targeted(
    kind: ChangeKind,
    target_ref: str,
    resolved: Task | None,
    *,
    fields: dict[str, object] | None = None,
    confidence: float,
    rationale: str,
) -> ProposedChange:
    """Build a change against an existing task.

    ``target_id`` stays ``None`` when the reference did not resolve. That is not a
    failure to handle -- it is the plan carrying its own refusal, so the orchestrator
    reports this instruction as failed instead of quietly succeeding on the others.
    """
    return ProposedChange(
        kind=kind,
        fields=fields or {},
        target_ref=target_ref,
        target_id=resolved.task_id if resolved is not None else None,
        confidence=confidence,
        rationale=rationale,
    )
