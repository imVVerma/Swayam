"""LLM-backed intent router.

Implements the same ``IntentRouter`` protocol as ``DeterministicRouter``, so the two are
interchangeable and the orchestrator cannot tell them apart. The protocol's shape is the
contract; neither implementation is canonical.

What the model is and is not allowed to do:

- It may read the whole sentence and say what the user asked for, including several
  instructions in one utterance and the exact words that make up a title. That is the
  part regexes are bad at.
- It may not decide whether the system acts. Every output is checked here before it
  reaches the orchestrator: instructions whose quoted span does not appear in the
  utterance are dropped, confidence is bounded, and an empty instruction list is
  refused. The model's job is reading, not authorising.

``quote`` is the load-bearing field. Every instruction must carry the exact span it came
from, and that span is verified against the utterance. It gives coverage a checkable
definition -- a model cannot invent an instruction the user never typed -- and it means
a dropped instruction is *detectable*, which is what lets the orchestrator report the gap
instead of quietly proceeding.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from swayam.core.errors import SwayamError
from swayam.llm.port import LLM
from swayam.reasoning.router.deterministic import _ACTION_HEADS, STATE_WORDS, IntentRouter
from swayam.reasoning.router.intent import Intent, IntentDecision

ROUTER_PURPOSE = "router.classify"

# Derived from the deterministic router so the two cannot disagree about what actions
# exist. This used to be a hand-written Literal: a new action added to the deterministic
# router would have been unreachable through the LLM path, and a typo here would have been
# a runtime validation error rather than a missing branch.
#
# Head labels alone are not the full vocabulary -- the "update" head is refined by the
# clause's trailing state word into "complete" or "reopen" at dispatch, so those two are
# reachable actions that no head is named after. Both sources are folded in.
#
# "create_event" is not a verb head but an action type the LLM uses for calendar events.
ACTION_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(
        [action for action, _ in _ACTION_HEADS] + list(set(STATE_WORDS.values())) + ["create_event"]
    )
)
Action = Literal[ACTION_NAMES]  # type: ignore[valid-type]

INSTRUCTION_PROMPT = """\
Split the user's sentence into the distinct instructions it contains.

Rules:
- One entry per instruction. A sentence with two instructions has two entries.
- "quote" must be a verbatim substring of the sentence that instruction came from.
- "title" is the user's own words for a new task. Do not add, drop or reorder words,
  and do not include the date phrasing.
- "target_ref" keeps the user's phrasing for an existing task, articles included.
- "due_date" is ISO 8601 (YYYY-MM-DD), already resolved against the supplied "today".
- "blocked_by" is the dependency the user named, not your guess at one.
- If the sentence is not a request to change something, use an empty instruction list.
"""


class Instruction(BaseModel):
    """One instruction, as read by the model."""

    action: Action
    quote: str = Field(min_length=1)
    target_ref: str | None = None
    title: str | None = None
    new_title: str | None = None
    due_date: str | None = None
    blocked_by: str | None = None
    body: str | None = None


class RouteOutput(BaseModel):
    """The only shape the model is allowed to answer in."""

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    instructions: list[Instruction] = Field(default_factory=list)
    rationale: str = ""


class LLMRouter:
    def __init__(self, llm: LLM, *, fallback: IntentRouter | None = None) -> None:
        self._llm = llm
        self._fallback = fallback

    async def route(self, utterance: str, *, today: date) -> IntentDecision:
        try:
            output = await self._llm.complete(
                purpose=ROUTER_PURPOSE,
                instruction=INSTRUCTION_PROMPT,
                payload={"utterance": utterance, "today": today.isoformat()},
                schema=RouteOutput,
            )
        except (LookupError, SwayamError) as exc:
            # A provider that is down must not become a wrong answer. Hand over to the
            # deterministic router, which is always available and never guesses either.
            if self._fallback is not None:
                return await self._fallback.route(utterance, today=today)
            return IntentDecision(
                Intent.UNCLEAR, 0.0, rationale=f"model unavailable: {exc}"
            )

        return self._to_decision(output, utterance, today)

    def _to_decision(self, output: RouteOutput, utterance: str, today: date) -> IntentDecision:
        lowered = utterance.lower()
        grounded: list[dict[str, object]] = []
        for instruction in output.instructions:
            if instruction.quote.lower() not in lowered:
                # Not in the sentence, so not something the user said. Dropped rather
                # than trusted; the count below keeps the drop visible to the reply.
                continue
            grounded.append(instruction.model_dump(exclude={"quote"}))

        is_mutation = output.intent in (Intent.MUTATION, Intent.CAPTURE)
        if is_mutation and not grounded:
            return IntentDecision(
                Intent.UNCLEAR,
                0.0,
                rationale="model claimed a mutation but produced no grounded instruction",
            )

        slots: dict[str, object] = {"due_date": None}
        if grounded:
            slots.update(grounded[0])
        slots["instructions"] = grounded
        # Counted *before* grounding, so an ungrounded instruction still registers as a
        # gap and gets reported instead of vanishing.
        slots["instruction_count"] = len(output.instructions) or len(grounded)
        return IntentDecision(
            output.intent,
            output.confidence,
            slots,
            rationale=output.rationale or "llm routing",
        )
