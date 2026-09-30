"""Deterministic intent router (tier 0).

Rule-based and ordered. This is the shipping implementation for M0-M2, not a placeholder:
it makes the mock runtime fully reproducible and keeps routing cost at zero, which is
what lets the conversational contract be tested exhaustively before a model is involved.

``LLMRouter`` implements the same protocol. Neither is canonical -- the protocol is.

Two properties this file owes the rest of the pipeline, both of which were real bugs:

- **No fragment is ever dropped.** Clause splitting is only trusted when *every*
  resulting fragment independently reads as an instruction. "add a task to write the
  research and interviews" contains an "and", but the second half is not an instruction,
  so the split is rejected and the whole thing stays one title. Guessing that a
  conjunction is a separator is how half a user's sentence gets discarded.
- **Date phrasing never leaks into a title.** Removal is delegated to
  ``router.dates``, the same table that resolved the date, so the two cannot drift.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Protocol, runtime_checkable

from swayam.reasoning.router.dates import extract_date, strip_date_phrases
from swayam.reasoning.router.intent import Intent, IntentDecision

# Admission gate, read from `_ACTION_HEADS` itself. This used to be a separate
# hand-written tuple of verbs, which is how "remind me to call the vendor" and "i need to
# submit the assignment" ended up parseable by the dispatcher but rejected by the gate
# before the dispatcher ever ran: two lists describing one thing, kept in sync by hand.
# ---------------------------------------------------------------------- vocabulary
#
# The state words a clause can end with, and the action each one asks for. One table,
# because this was previously four overlapping lists -- an admission-gate tuple, a trailing
# stripper, and two action patterns -- with three different word sets. They disagreed twice:
# "closed" was strippable but not an admission trigger, and "not done" was listed as a
# reopen while the complete pattern was tested first, so "mark the report not done"
# completed it.
#
# A word maps to the action it *asserts*. Negation is handled once, in `resolve_state`, so
# that "not done", "not finished" and "not complete" all mean reopen instead of each
# needing their own entry (and each having to be remembered when the patterns are read).

COMPLETE_STATES = ("done", "finished", "complete", "completed", "shipped", "closed")
OPEN_STATES = ("open", "in progress")
STATE_WORDS: dict[str, str] = dict.fromkeys(COMPLETE_STATES, "complete") | dict.fromkeys(
    OPEN_STATES, "reopen"
)

_COMPLETE_WORDS = "|".join(COMPLETE_STATES)
_OPEN_WORDS = "|".join(OPEN_STATES)
_ALL_STATE_WORDS = "|".join(STATE_WORDS)

# Word-boundary forms. A bare substring test would match "complete" inside "incomplete",
# which is not a completion.
COMPLETION_WORD = re.compile(rf"\b(?:{_COMPLETE_WORDS})\b")

# "mark X done" is a completion; "mark X not done" is a reopen. The negated form has to be
# recognised as a unit or the complete pattern matches the "done" inside it.
_STATE_COMPLETE = re.compile(rf"\s+(?:as\s+)?(?:{_COMPLETE_WORDS})\s*$")
_STATE_OPEN = re.compile(
    rf"\s+(?:as\s+)?(?:{_OPEN_WORDS}|not\s+(?:{_COMPLETE_WORDS}))\s*$"
)
# Strips a trailing state word from the *reference*, so "the report not done" does not
# leave "not" behind as part of the task name.
TRAILING_STATE = re.compile(
    rf"\s+(?:as\s+)?(?:not\s+)?(?:{_ALL_STATE_WORDS})\s*$"
)


def resolve_state(text: str) -> str | None:
    """The action a clause's trailing state word asks for, or ``None``.

    One place, rather than testing two patterns in sequence. The ordering mattered and was
    invisible: "not done" contains "done", so a complete-first check turned an instruction
    to undo something into an instruction to perform it.
    """
    if _STATE_OPEN.search(text):
        return "reopen"
    if _STATE_COMPLETE.search(text):
        return "complete"
    return None
STATE_MARKERS = ("today", "right now", "currently", "on my plate", "this week")
LOAD_MARKERS = ("overcommitted", "over-committed", "how busy", "load", "too much", "capacity")
TEMPORAL_MARKERS = ("last week", "this week", "next week", "i said", "planned", "supposed to")
CROSS_MARKERS = ("how did", "how's", "how has", "what happened", "catch me up", "summar")
SMALLTALK = {"hi", "hey", "hello", "thanks", "thank you", "yo", "ok", "okay", "sure"}

# Verbs that commit you to a task without naming the task as a task: "book train
# tickets", "renew the insurance", "email the vendor". Deliberately short. This list is
# where a wrong guess costs a junk task in the log, so it errs toward missing a verb the
# user can restate ("add a task to book train tickets") rather than capturing everything
# that starts with a word. The LLM router is the intended home for open-ended capture;
# this is the deterministic floor under it.
CAPTURE_VERB = re.compile(
    r"^(?:please\s+)?(?:book|renew|email|water|order|buy|pick up|drop off"
    r"|schedule|submit|pay|file|reply|confirm|cancel the|rebook)\b"
)

CAPTURE_LEAD = re.compile(
    r"^\s*(please\s+)?"
    r"(add|create|make|new|remind me|i need to|i have to|i must|log|capture)\s+"
    r"(a\s+|an\s+|the\s+)?(task|todo|to-do|note|reminder|item)?\s*"
    r"(to|for|that|about|on)?\s*"
)
NOTE_LEAD = re.compile(
    r"^\s*(please\s+)?(add|leave|make|write|put)?\s*(a\s+)?note\s*"
    r"(to|on|about|for)?\s*"
)
# Action is read from the *head* of the clause, where English puts the imperative.
# Scanning the whole clause is what let "add a note to the deck that it shipped" route as
# a completion, because "shipped" is also a completion word -- and it made "add a task to
# move the boxes" a reschedule, because "move" outranked "add". Content words inside the
# user's own object text must not be able to choose the verb.
_ACTION_HEADS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("note", re.compile(r"^(?:please\s+)?(?:(?:add|leave|make|write|put)\s+)?(?:a\s+)?notes?\b")),
    (
        "create",
        re.compile(
            r"^(?:please\s+)?(?:add|create|new|log|capture|remind me"
            r"|i need to|i have to|i must)\b"
        ),
    ),
    (
        "reschedule",
        re.compile(
            r"^(?:please\s+)?"
            r"(?:pull forward|push back|move|push|reschedule|slip|postpone|shift|bump)\b"
        ),
    ),
    ("archive", re.compile(r"^(?:please\s+)?(?:archive|delete|cancel|scrub|abandon)\b")),
    ("rename", re.compile(r"^(?:please\s+)?(?:rename|re-?title|call it)\b")),
    ("block", re.compile(r"^(?:please\s+)?(?:block|blocked|waiting on|stuck on|hold on)\b")),
    (
        "reopen",
        # 'unblock' is the same instruction as 'reopen' for a blocked task: both mean
        # make it open work again. Shipping blocking without unblocking would have been a
        # one-way door.
        re.compile(r"^(?:please\s+)?(?:re-?open|un-?block|un-?complete|unmark|start again)\b"),
    ),
    ("prioritise", re.compile(r"^(?:please\s+)?prioriti[sz]e?\b")),
    # 'fix' heads an update ("fix the server due friday"), but it is also a bare task verb
    # ("fix the server" alone). Bare-verb capture is checked first and wins, so the update
    # reading applies only when the phrase is already a known task reference.
    ("update", re.compile(r"^(?:please\s+)?(?:set|change|update|mark|bump)\b")),
)

def _leading_verb() -> re.Pattern[str]:
    """Strip the clause's leading action verb, leaving the reference.

    Built from ``_ACTION_HEADS`` rather than transcribed. It was a fourth hand-written copy
    of the verb vocabulary: it listed "finish" and "complete", which no head produces, and
    omitted "abandon", "log" and "scrub", which heads do -- so those were left in the
    reference text as though they were part of the task's name.

    Each head is an anchored ``^(?:please )?(?:a|b|c)\\b``; dropping the anchor and the word
    boundary leaves a self-contained pattern body, and bodies nest, so they union directly.
    """
    bodies = []
    for _, pattern in _ACTION_HEADS:
        body = pattern.pattern.removeprefix(r"^(?:please\s+)?").removesuffix(r"\b")
        bodies.append(body)
    # Longest bodies first: "push back" is two words, and if the bare "push" alternative
    # were tried first it would consume only that, leaving "back the old deck" as the
    # reference. Regex alternation is first-match-wins, so order carries meaning here.
    bodies.sort(key=len, reverse=True)
    return re.compile(rf"^\s*(please\s+)?(?:{'|'.join(bodies)})\s+")


LEADING_VERB = _leading_verb()

# 'mark X done' is a completion and 'mark X open' is a reopen. The leading verb alone does
# not say which, so these heads are refined by the clause's final state word via
# resolve_state above.

# Separators a user might use between two instructions. Splitting on these is only ever
# a *proposal*; see _split_clauses for when it is believed.
_CLAUSE_SPLIT = re.compile(
    r"\s*(?:;\s*|,\s*(?:and\s+|also\s+|then\s+)?|\s+and\s+also\s+|\s+and\s+then\s+|\s+and\s+)\s*"
)

# A fragment has to carry its own verb to count as an instruction in its own right.
# Without this, "mark slides done, also move the report to friday" splits into two
# fragments but the second has no verb of its own and gets absorbed, silently dropping it.
#
# This asks the dispatcher itself rather than a second hand-written verb list. The list was
# 12 heads behind _ACTION_HEADS, so "add a task to send the q3 report, abandon the old
# deck" collapsed into one task titled "send the q3 report, abandon the old deck" -- the
# exact silent-drop this module's docstring promises cannot happen.
def _fragment_has_verb(fragment: str) -> bool:
    # A bare completion is an instruction too ("mark slides done, unmark the deck").
    if _has_action_head(fragment):
        return True
    return COMPLETION_WORD.search(fragment) is not None


def _has_action_head(text: str) -> bool:
    """True when the utterance *opens* with a known action head.

    Head-anchored on purpose: scanning the whole string made "add a note to the deck that
    it shipped" a completion, because "shipped" appears in it.
    """
    return any(pattern.search(text) is not None for _, pattern in _ACTION_HEADS)


def _looks_like_mutation(text: str) -> bool:
    """Whether this utterance is asking for a change.

    Derived from the dispatcher and the state vocabulary rather than a third hand-written
    verb list. Every state word is a mutation signal, which is what makes "the q3 report is
    done" a mutation without opening with a verb.
    """
    if _has_action_head(text) or CAPTURE_VERB.match(text):
        return True
    return any(w in text for w in STATE_WORDS)


@runtime_checkable
class IntentRouter(Protocol):
    async def route(self, utterance: str, *, today: date) -> IntentDecision: ...


class DeterministicRouter:
    def __init__(self, *, rules: Sequence[tuple[str, Intent, float]] | None = None) -> None:
        self._rules = list(rules) if rules else None

    async def route(self, utterance: str, *, today: date) -> IntentDecision:
        text = utterance.strip().lower()
        if not text:
            return IntentDecision(Intent.UNCLEAR, 0.0, rationale="empty utterance")
        if text.rstrip("!.?") in SMALLTALK:
            return IntentDecision(Intent.SMALLTALK, 0.95, rationale="greeting or acknowledgement")

        due = extract_date(text, today=today)
        slots: dict[str, object] = {"due_date": due.isoformat() if due else None}

        # Mutation is checked before queries: "move the grant draft to Thursday, when is
        # it due?" is a mutation that happens to contain a question.
        # An action head anchors the utterance as a mutation ("add a note to the deck
        # that it shipped" opens with "add"). The completion-word check is separate and
        # deliberately not head-anchored: "the q3 report is done" states a completion
        # without opening with a verb, and losing it would be a silent regression.
        if _looks_like_mutation(text):
            return self._route_mutation(text, today)

        if any(marker in text for marker in CROSS_MARKERS):
            return IntentDecision(
                Intent.CROSS_DOMAIN, 0.72, slots, rationale="retrospective synthesis phrasing"
            )
        if any(marker in text for marker in LOAD_MARKERS):
            return IntentDecision(
                Intent.LOAD_QUERY, 0.85, slots, rationale="capacity/load phrasing"
            )
        if any(marker in text for marker in TEMPORAL_MARKERS):
            return IntentDecision(
                Intent.TEMPORAL_QUERY, 0.80, slots, rationale="time-scoped reference to history"
            )
        if any(marker in text for marker in STATE_MARKERS):
            return IntentDecision(
                Intent.STATE_QUERY, 0.80, slots, rationale="present-state phrasing"
            )

        return IntentDecision(
            Intent.UNCLEAR, 0.20, slots, rationale="no rule matched; do not guess"
        )

    # ------------------------------------------------------------------ mutations

    def _route_mutation(self, text: str, today: date) -> IntentDecision:
        clauses = _split_clauses(text)
        instructions = [_route_clause(clause, today) for clause in clauses]
        slots: dict[str, object] = {
            # Promoted from the single instruction so the top-level contract is unchanged
            # for the one-instruction case that most callers read.
            **instructions[0],
            "instructions": instructions,
            "instruction_count": len(instructions),
        }
        confidence = 0.82 if any(
            i.get("title") or i.get("target_ref") or i.get("due_date") for i in instructions
        ) else 0.40
        if len(instructions) > 1:
            # Several instructions are read as several instructions, not as one
            # ambiguous one -- but the confidence is a little lower, because a clause
            # boundary is a guess even when each clause is clear.
            confidence = min(confidence, 0.78)
        return IntentDecision(
            Intent.MUTATION,
            confidence,
            slots,
            rationale=f"{len(instructions)} mutation instruction(s)",
        )


def _split_clauses(text: str) -> list[str]:
    parts = [p.strip(" ,.;") for p in _CLAUSE_SPLIT.split(text) if p.strip(" ,.;")]
    if len(parts) < 2:
        return [text]
    # Only believe the split when every fragment stands alone. If one does not, the
    # conjunction belongs to a title and splitting would silently drop half the sentence.
    if all(_fragment_has_verb(part) for part in parts):
        return parts
    return [text]


def _route_clause(text: str, today: date) -> dict[str, object]:
    slots: dict[str, object] = {}
    due = extract_date(text, today=today)
    slots["due_date"] = due.isoformat() if due else None

    action = _detect_action(text)
    slots["action"] = action

    if action == "create" or (action is None and CAPTURE_VERB.match(text)):
        # "book train tickets due october 19" has no "add a task" wrapper to peel, so the
        # leading verb stays part of the title -- "book train tickets" is what the user
        # wants to see in a list.
        title = strip_date_phrases(CAPTURE_LEAD.sub("", text), today=today).strip(" .,")
        # If stripping consumed nothing, the "title" is just the verb back again
        # ("add"). That is not something to act on, so do not claim confidence.
        if title and (title != text or action is None):
            slots["title"] = title
        return slots

    if action == "note":
        body = NOTE_LEAD.sub("", text)
        target, note_body = _split_note(body)
        if target:
            slots["target_ref"] = target
        if note_body:
            slots["body"] = note_body
        return slots

    rest = TRAILING_STATE.sub("", LEADING_VERB.sub("", text)).strip(" .,")
    rest = strip_date_phrases(rest, today=today)
    if not rest or rest == text:
        return slots

    if action == "rename":
        target, _, new_title = rest.partition(" to ")
        # The target is recorded even when the new title is missing, because then the
        # right reply is "what should I call it instead?" -- and asking that requires
        # knowing which task was meant.
        if target:
            slots["target_ref"] = target.strip()
        if new_title:
            slots["new_title"] = new_title.strip()
        return slots

    if action == "block":
        target, blocker = _split_blocked_by(rest, today=today)
        if target:
            slots["target_ref"] = target.strip()
        if blocker:
            slots["blocked_by"] = blocker.strip()
        return slots

    slots["target_ref"] = rest
    return slots


# The fallback is None, not "update". Returning "update" here meant every utterance with
# no recognised head was read as a request to change something, which is how "book train
# tickets due october 19" became an attempt to reschedule a task nobody had mentioned.
_UNRESOLVED = None


def _detect_action(text: str) -> str | None:
    for action, pattern in _ACTION_HEADS:
        if pattern.search(text):
            if action == "update":
                # "mark X done" vs "mark X open": the leading verb does not say which, so
                # the clause's trailing state word decides.
                state = resolve_state(text)
                if state is not None:
                    return state
            return action
    return _UNRESOLVED


def _split_note(text: str) -> tuple[str, str | None]:
    """'to the grant draft that the budget landed' -> ('the grant draft', 'the budget
    landed'). Without the connective there is nothing to attach a note to, so the whole
    phrase stays a reference and the planner asks."""
    parts = re.split(r"\s+(?:that|about|:)\s+", text, maxsplit=1)
    target = parts[0].strip()
    body = parts[1].strip() if len(parts) > 1 else ""
    return target, body or None


def _split_blocked_by(text: str, *, today: date) -> tuple[str, str | None]:
    """'the grant draft because the budget is late' -> ('the grant draft', 'the budget is
    late'). 'until' counts too, unless what follows is a date -- 'block X until Friday'
    is scheduling, not a dependency, and the domain will reject a fabricated reason."""
    for keyword in (r"\bbecause\b", r"\bblocked on\b", r"\bblocked by\b",
                    r"\bwaiting on\b", r"\bpending\b"):
        parts = re.split(keyword, text, maxsplit=1)
        if len(parts) > 1:
            return parts[0], parts[1]
    parts = re.split(r"\buntil\b", text, maxsplit=1)
    if len(parts) > 1 and not extract_date(parts[1], today=today):
        return parts[0], parts[1]
    return text, None
