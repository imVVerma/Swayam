"""Mutation proposals.

The most important safety property in Phase 1: **the model never writes to the event
log.** A planner emits a ``ProposedChange`` -- a plain data structure with no side
effects -- which the domain validates like any other command. A malformed plan is a
validation failure, not a corrupted aggregate.

Because a proposal is data rather than a function call, it can be logged, diffed, shown
to the user for confirmation, and unit-tested with no database and no model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


class ChangeKind(StrEnum):
    CREATE_TASK = "create_task"
    CREATE_EVENT = "create_event"
    SCHEDULE_TASK = "schedule_task"
    COMPLETE_TASK = "complete_task"
    REOPEN_TASK = "reopen_task"
    BLOCK_TASK = "block_task"
    ARCHIVE_TASK = "archive_task"
    ADD_NOTE = "add_note"
    RENAME_TASK = "rename_task"


REQUIRED_FIELDS: dict[ChangeKind, tuple[str, ...]] = {
    ChangeKind.CREATE_TASK: ("title",),
    ChangeKind.CREATE_EVENT: ("title", "starts_at", "ends_at"),
    ChangeKind.SCHEDULE_TASK: ("due_date",),
    ChangeKind.BLOCK_TASK: ("blocked_by",),
    ChangeKind.RENAME_TASK: ("new_title",),
    ChangeKind.ADD_NOTE: ("body",),
}
"""Fields a change cannot be built without.

Authoritative. Both planners read it, and so does the question-asking path, so a new
``ChangeKind`` cannot acquire a required field that the clarification text forgets.
"""

ACTION_KIND: dict[str, ChangeKind] = {
    "create": ChangeKind.CREATE_TASK,
    "create_event": ChangeKind.CREATE_EVENT,
    "reschedule": ChangeKind.SCHEDULE_TASK,
    "block": ChangeKind.BLOCK_TASK,
    "rename": ChangeKind.RENAME_TASK,
    "note": ChangeKind.ADD_NOTE,
    "complete": ChangeKind.COMPLETE_TASK,
    "reopen": ChangeKind.REOPEN_TASK,
    "archive": ChangeKind.ARCHIVE_TASK,
}
"""The action string the router emits, mapped to the change it produces.

This mapping used to exist only as the order of ``if action == ...`` branches inside the
rule planner, so nothing else could ask what a given action was missing.
"""

MISSING_FIELD_QUESTION: dict[str, str] = {
    "title": "What should the task be?",
    "due_date": "When should it be due?",
    "blocked_by": "What is it blocked on?",
    "new_title": "What should I call it instead?",
    "body": "What should the note say?",
    "starts_at": "When does the event start?",
    "ends_at": "When does the event end?",
}


def missing_field_question(action: str, instruction: dict[str, Any]) -> str | None:
    """The question to ask when ``action`` could not be planned, or ``None``.

    ``None`` means "nothing required is actually missing", so a fully-specified instruction
    never gets diverted into a clarification. A missing *target* is deliberately not
    handled here: "which task?" is a different question from "what is it blocked on?", and
    only the caller knows whether the reference resolved.
    """
    kind = ACTION_KIND.get(action)
    if kind is None:
        return None
    # Actions that don't require a target_ref (they create new things)
    no_target_actions = {"create", "create_event"}
    for field_name in REQUIRED_FIELDS.get(kind, ()):
        if instruction.get(field_name):
            continue
        needs_target = field_name != "title" and not instruction.get("target_ref")
        if needs_target and action not in no_target_actions:
            # The reference is absent or unresolved; that is the "which task?" case.
            return None
        return MISSING_FIELD_QUESTION.get(field_name, f"What should {field_name} be?")
    return None


@dataclass(frozen=True, slots=True)
class ProposedChange:
    kind: ChangeKind
    fields: dict[str, Any] = field(default_factory=dict)
    target_ref: str | None = None
    """Free-text reference the user used, e.g. "the grant draft". Resolved to a
    concrete aggregate id by the planner *before* this leaves the reasoning layer --
    an unresolved reference is a clarification, never a best-effort match."""
    target_id: UUID | None = None
    confidence: float = 0.0
    rationale: str = ""

    def describe(self) -> str:
        if self.target_ref and not self.target_id:
            return f"{self.kind.value} (unresolved: {self.target_ref!r})"
        return self.kind.value
