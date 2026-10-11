"""Turn a completed turn into the observations a fixture can assert on.

This is the only place the eval suite reads system behaviour, and it reads it the way
a user would: what the reply said, how many events landed, what the task list looks
like now. Nothing here reaches into planner internals, because a gate that couples to
implementation details stops being a gate the moment the implementation improves.

The one thing it does borrow from the implementation is the *question set*: the
``MISSING_FIELD_QUESTION`` table and the orchestrator's fixed prompts. Deriving from
those means adding a required field cannot silently turn every eval for it into a
mystery.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from swayam.conversation.turn import NOT_YET, TurnResult  # typos fixed below
from swayam.reasoning.mutation.proposal import MISSING_FIELD_QUESTION
from swayam.reasoning.router.intent import Intent

from .spec import ClarifiesAbout, Outcome

# Phrases the orchestrator uses when it could not identify a task. Substring matching
# rather than equality because these are embedded in longer sentences alongside the
# candidate list.
_UNRESOLVED_TARGET = ("could not tell which task", "which task")
_ASK_WHICH_TASK = ("which task?", "which task, and what")

_FIELD_BY_QUESTION: dict[str, str] = {
    question: field_name for field_name, question in MISSING_FIELD_QUESTION.items()
}


@dataclass(frozen=True, slots=True)
class Observation:
    outcome: Outcome
    commits: int
    clarifies_about: ClarifiesAbout
    required_field: str | None
    reply_text: str
    clarification: str | None
    asks_which_task: bool
    offers_candidates: bool
    wrote_blocker: bool
    wrote_due_date: bool
    wrote_note: bool
    wrote_title: bool
    task_titles: tuple[str, ...]
    instruction_count: int | None
    proposals: int
    violations: tuple[str, ...] = field(default=())


def _clarification_kind(clarification: str | None, *, reply_text: str) -> tuple[
    ClarifiesAbout, str | None
]:
    if clarification is None:
        return "none", None
    lowered = clarification.lower()
    if "rephrase" in lowered:
        return "low_confidence", None
    for question, field_name in _FIELD_BY_QUESTION.items():
        if question.lower() in lowered:
            return "required_field", field_name
    if any(phrase in lowered for phrase in _UNRESOLVED_TARGET):
        return "target", None
    if reply_text == NOT_YET or "arrives in m3" in lowered:
        return "unexecutable", None
    if "date" in lowered or "when" in lowered:
        return "date", None
    return "unexecutable", None


def observe(result: TurnResult, *, task_titles: tuple[str, ...]) -> Observation:
    reply = result.reply
    kind, required_field = _clarification_kind(reply.clarification, reply_text=reply.text)
    lowered_clarification = (reply.clarification or "").lower()
    asks_which_task = any(phrase in lowered_clarification for phrase in _ASK_WHICH_TASK)
    offers_candidates = "closest:" in lowered_clarification

    event_names = [recorded.event.type for recorded in result.committed]
    payloads = [recorded.event.payload for recorded in result.committed]

    wrote_blocker = any(payload.get("blocked_by") for payload in payloads)
    wrote_due_date = any(payload.get("due_date") for payload in payloads)
    wrote_note = any(payload.get("body") for payload in payloads)
    wrote_title = any(payload.get("title") for payload in payloads)

    if result.committed and reply.clarification:
        outcome: Outcome = "partial"
    elif result.committed:
        outcome = "captured" if "task.created" in event_names else "applied"
    elif reply.text == NOT_YET:
        outcome = "declined"
    elif reply.clarification is not None:
        outcome = "clarified"
    elif result.intent is Intent.SMALLTALK:
        outcome = "smalltalk"
    else:
        outcome = "declined"

    proposals = 0
    instruction_count: int | None = None
    if result.trace is not None:
        for entry in result.trace.entries:
            if entry.name == "plan":
                proposals = len(entry.data.get("proposals", []))
            if entry.name == "route":
                pass
    if result.decision is not None:
        raw = result.decision.slots.get("instruction_count")
        instruction_count = raw if isinstance(raw, int) else None

    violations: list[str] = []
    if result.committed and result.reply.clarification and instruction_count in (None, 0):
        violations.append("silent_partial")
    if outcome == "partial" and not result.reply.clarification:
        violations.append("silent_partial")

    return Observation(
        outcome=outcome,
        commits=len(result.committed),
        clarifies_about=kind,
        required_field=required_field,
        reply_text=reply.text,
        clarification=reply.clarification,
        asks_which_task=asks_which_task,
        offers_candidates=offers_candidates,
        wrote_blocker=wrote_blocker,
        wrote_due_date=wrote_due_date,
        wrote_note=wrote_note,
        wrote_title=wrote_title,
        task_titles=task_titles,
        instruction_count=instruction_count,
        proposals=proposals,
        violations=tuple(violations),
    )


def observed_forbidden(observation: Observation) -> set[str]:
    found: set[str] = set()
    if observation.commits and observation.outcome == "clarified":
        found.add("mutate_any_task")
    if observation.asks_which_task and observation.clarifies_about != "target":
        found.add("ask_which_task")
    if observation.wrote_blocker and observation.clarifies_about == "required_field":
        found.add("invent_blocker")
    if observation.wrote_due_date and observation.clarifies_about == "date":
        found.add("invent_due_date")
    if observation.wrote_note and observation.clarifies_about == "required_field":
        found.add("invent_body")
    if observation.wrote_title and observation.clarifies_about == "required_field":
        found.add("invent_title")
    found.update(observation.violations)
    return found
