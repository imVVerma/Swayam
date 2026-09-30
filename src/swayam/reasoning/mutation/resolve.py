"""Free-text reference resolution.

One implementation, used by both planners, so the LLM path and the deterministic path
cannot disagree about what "the report" points at. If they did, the same sentence would
rewrite different tasks depending on which router ran -- the kind of inconsistency that
only shows up as lost work.

The rule throughout: **zero matches and several matches are both no matches.** Choosing
between two plausible tasks is how a system silently rewrites the wrong commitment, and
that failure is invisible until someone notices the wrong deadline moved.
"""

from __future__ import annotations

from collections.abc import Sequence

from swayam.domain.task import Task

# Words that are never part of a *matching key*. Never applied to a title the user is
# creating -- only to the string on the left of a comparison.
STOPWORDS = {
    "a", "an", "the", "to", "for", "on", "at", "in", "by", "my", "i", "me",
    "task", "todo", "it", "that", "this", "and", "please", "need", "should",
}


def clean_title(text: str) -> str:
    words = [w for w in text.strip().lower().split() if w not in STOPWORDS]
    return " ".join(words).strip()


def live_tasks(known_tasks: Sequence[Task]) -> list[Task]:
    """Tasks a reference is allowed to point at.

    Archived tasks are excluded: they are history, and a reference to one should ask rather
    than mutate something the user believes is out of the way.
    """
    return [t for t in known_tasks if t.exists and not t.archived]


def match_candidates(reference: str, known_tasks: Sequence[Task]) -> list[Task]:
    """Every live task this reference could plausibly mean, best first.

    The single matching rule. ``resolve_task`` and ``describe_candidates`` are both built
    on this; they used to each carry their own copy, and the copies disagreed -- one cleaned
    both sides of the comparison and the other only the reference, and only one kept the
    exact-match tier. The user was shown a candidate list missing the task whose title was
    literally the phrase they had typed.
    """
    needle = clean_title(reference)
    live = live_tasks(known_tasks)
    if not needle or not live:
        return []

    # Both sides are cleaned. Cleaning only the needle compares "send report" against the
    # raw "send the report" and misses, which is why "the fix the server task" failed to
    # match the task literally titled "fix the server".
    exact = [t for t in live if t.title and clean_title(t.title) == needle]
    if exact:
        # An exact title is a better answer than any substring overlap, so it wins outright.
        return exact

    partial = []
    for t in live:
        title = clean_title(t.title or "")
        if title and (needle in title or title in needle):
            partial.append(t)
    return partial


def resolve_task(reference: str, known_tasks: Sequence[Task]) -> Task | None:
    """Match a free-text reference to exactly one task, or return ``None``."""
    matches = match_candidates(reference, known_tasks)
    return matches[0] if len(matches) == 1 else None


def describe_candidates(reference: str, known_tasks: Sequence[Task]) -> str:
    """Human-readable candidate list for a clarification.

    Quoting back the tasks the user might have meant is what turns "I didn't understand"
    into a question they can answer in one word. Shares its matching rule with
    ``resolve_task``, so the list shown can never disagree with the resolution attempted.
    """
    matches = match_candidates(reference, known_tasks)
    if not matches:
        live = live_tasks(known_tasks)
        titles = [t.title for t in live if t.title]
        return "; ".join(titles) if titles else (
            "none of your tasks" if live else "you have no tasks yet"
        )
    return "; ".join(t.title for t in matches if t.title)
