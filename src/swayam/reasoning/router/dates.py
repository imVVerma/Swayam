"""Date phrase resolution *and* removal.

Phase 1 needs to turn "next Thursday", "in 3 days" and "October 19" into real dates,
and to keep those same phrases out of the task title they were attached to.

The two jobs share one table of rules on purpose. Extraction and removal drifting apart
is exactly how "due in 3 days" ends up half-consumed and stored as a task called
"review the budget due". A phrase ``extract_date`` can resolve is a phrase
``strip_date_phrases`` knows how to remove, because they are the same list.

Three deliberate choices:

- Ambiguity resolves to ``None``, not a guess. "sometime next week" is a clarification,
  not a date.
- Weekday resolution is forward-looking only: "Thursday" means the next Thursday,
  including today if today is Thursday. "Last Thursday" is handled separately because it
  is a retrospective question, which the fast path answers from history rather than
  scheduling.
- Removal is conservative. A date phrase leaves a title only when it is self-evident
  ("in 3 days", "October 19"), anchored by a scheduling preposition ("due friday", "to
  Thursday"), or trailing. That keeps "Friday standup sync" intact.

The cost of that last rule is real: a title that *ends* in a date word, like
"task called friday", will lose the word. The user has to rephrase, or go through the
LLM router, which reads the whole sentence. This table is the floor, not the ceiling.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_DAY_ALT = "|".join(WEEKDAYS)
_MONTH_ALT = "|".join(MONTHS)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


# ---------------------------------------------------------------------- resolvers


def _shift(days: int) -> Callable[[re.Match[str], date], date | None]:
    return lambda _match, today: today + timedelta(days=days)


def _relative(match: re.Match[str], today: date) -> date | None:
    """'in 3 days' / 'in 2 weeks' / 'in 6 months'."""
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("day"):
        return today + timedelta(days=amount)
    if unit.startswith("week"):
        return today + timedelta(weeks=amount)
    # Month arithmetic clamps rather than overflowing: "in 1 month" on 31 January is the
    # 28th of February, not an invalid date the user has to hear about.
    months = today.month - 1 + amount
    year = today.year + months // 12
    month = months % 12 + 1
    return date(year, month, min(today.day, _days_in_month(year, month)))


def _next_weekday(match: re.Match[str], today: date) -> date | None:
    """'next Thursday' means the Thursday of *next* week, not the one three days away.

    Documented rather than clever: the alternative reading is common enough in speech
    that guessing here would be the wrong default.
    """
    start_of_next_week = today + timedelta(days=7 - today.weekday())
    return start_of_next_week + timedelta(days=WEEKDAYS[match.group(1)])


def _forward_weekday(match: re.Match[str], today: date) -> date | None:
    """'this Friday', 'on Monday', or a bare 'Thursday'."""
    qualifier, day_name = match.group(1), match.group(2)
    delta = (WEEKDAYS[day_name] - today.weekday()) % 7
    if delta == 0 and qualifier is None:
        # A bare weekday meaning today is a real ambiguity. Refusing it is cheaper than
        # being confidently wrong, which is the failure this system must avoid.
        return None
    return today + timedelta(days=delta)


def _month_day(match: re.Match[str], today: date) -> date | None:
    # Both orders are accepted: "October 19" and "19 October" are the same instruction.
    name, number = (
        (match.group(1), match.group(2))
        if match.group(1)
        else (match.group(4), match.group(3))
    )
    month, day = MONTHS[name], int(number)
    resolved = _safe_date(today.year, month, day)
    if resolved is None:
        return None
    if resolved < today:
        return _safe_date(today.year + 1, month, day)
    return resolved


def _day_of_month(match: re.Match[str], today: date) -> date | None:
    day = int(match.group(1) or match.group(2))
    if not 1 <= day <= 31:
        return None
    resolved = _safe_date(today.year, today.month, day)
    if resolved is None:
        return None
    if resolved < today:
        month = today.month + 1
        year = today.year
        if month > 12:
            month, year = 1, year + 1
        resolved = _safe_date(year, month, day)
    return resolved


# ---------------------------------------------------------------------- rule table


@dataclass(frozen=True, slots=True)
class DateRule:
    name: str
    pattern: re.Pattern[str]
    resolve: Callable[[re.Match[str], date], date | None]
    self_evident: bool = False
    """True when the phrase cannot plausibly be title content, so it may be stripped even
    with no preposition to anchor it. 'in 3 days' and 'October 19' qualify; a bare
    'friday' does not, because 'Friday standup' is a title."""


# Ordered most specific first. The first rule that *matches* decides the answer, even if
# it resolves to None -- continuing past an ambiguity would trade one guess for another.
RULES: tuple[DateRule, ...] = (
    DateRule("day_after_tomorrow", re.compile(r"\bday after tomorrow\b"), _shift(2), True),
    DateRule(
        "relative_offset",
        re.compile(r"\bin (\d{1,3}) (day|week|month)s?\b"),
        _relative,
        True,
    ),
    DateRule("tomorrow", re.compile(r"\btomorrow\b"), _shift(1)),
    DateRule("yesterday", re.compile(r"\byesterday\b"), _shift(-1)),
    DateRule("today", re.compile(r"\btoday\b"), _shift(0)),
    DateRule(
        "next_weekday",
        re.compile(rf"\bnext ({_DAY_ALT})\b"),
        _next_weekday,
        True,
    ),
    DateRule(
        "this_weekday",
        # The qualifier is captured as group 1 so this rule can share _forward_weekday
        # with the bare-weekday rule below. Dropping it would make "this Tuesday" and
        # "Tuesday" resolve identically, which is the ambiguity the rule exists to avoid.
        re.compile(rf"\b(this) ({_DAY_ALT})\b"),
        _forward_weekday,
        True,
    ),
    DateRule(
        "month_day",
        re.compile(
            rf"\b(?:({_MONTH_ALT})\.? (\d{{1,2}})(?:st|nd|rd|th)?"
            rf"|(\d{{1,2}})(?:st|nd|rd|th)? ({_MONTH_ALT})\b)"
        ),
        _month_day,
        True,
    ),
    DateRule(
        "weekday",
        re.compile(rf"\b(this|on|by|next)? ?({_DAY_ALT})\b"),
        _forward_weekday,
    ),
    DateRule(
        "day_of_month",
        re.compile(r"\b(?:the (\d{1,2})(?:st|nd|rd|th)|(\d{1,2})(?:st|nd|rd|th))\b"),
        _day_of_month,
    ),
)

# Date language that is recognisable but deliberately unresolvable. "this week" is a
# window, not a day, and turning it into one is the kind of confident misread that costs
# trust. These phrases are stripped from titles but never produce a date.
UNRESOLVABLE_DATE_LANGUAGE: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:this|next|last|coming) (?:week|month|fortnight|quarter|year)\b"),
    re.compile(r"\b(?:end of|start of) (?:the )?(?:week|month|quarter|year)\b"),
    re.compile(r"\bsometime\b\s*\w*"),
)


# ---------------------------------------------------------------------- extraction


def extract_date(text: str, *, today: date) -> date | None:
    """Best-effort date extraction. Returns ``None`` when nothing resolves cleanly."""
    lowered = text.lower()
    for rule in RULES:
        match = rule.pattern.search(lowered)
        if match is not None:
            return rule.resolve(match, today)
    return None


# ---------------------------------------------------------------------- removal

# A date phrase introduced by one of these is scheduling language, not title content.
# 'to' is included because 'move X to Thursday' is a reschedule, and it only ever fires
# adjacent to a phrase that already matched a date rule.
_SCHEDULING_PREFIX = re.compile(
    r"\b(?:due|by|on|to|until|for|before|after|from|starting|"
    r"deadline(?:\s+is)?)(?:\s+(?:the|a|an))?\s*$"
)
_DANGLING_TAIL = re.compile(
    r"(?:\s+(?:to|for|on|by|due|until|in|at|of|the|a|an))+$", re.IGNORECASE
)


def strip_date_phrases(text: str, *, today: date) -> str:
    """Remove the date phrasing from ``text``, leaving the rest intact.

    Conservative by design -- see the module docstring. A phrase goes only when it is
    self-evident, preposition-anchored, or trailing, so titles that merely *contain* a
    weekday ("Friday standup") survive.
    """
    spans: list[tuple[int, int]] = []
    for rule in RULES:
        for match in rule.pattern.finditer(text):
            if _should_strip(text, match, rule, today):
                spans.append((_expand_left(text, match.start()), match.end()))
    for pattern in UNRESOLVABLE_DATE_LANGUAGE:
        for match in pattern.finditer(text):
            spans.append((_expand_left(text, match.start()), match.end()))
    if not spans:
        return text.strip()

    merged = _merge(spans)
    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        pieces.append(text[cursor:start])
        cursor = end
    pieces.append(text[cursor:])
    return _tidy("".join(pieces))


def _should_strip(
    text: str, match: re.Match[str], rule: DateRule, today: date
) -> bool:
    if rule.self_evident:
        return True
    if _SCHEDULING_PREFIX.search(text[: match.start()]):
        return True
    # A date phrase at the very end of a clause is scheduling far more often than it is
    # content. Requiring it to resolve keeps the genuinely ambiguous cases (a bare
    # weekday spoken on that same weekday) in the title.
    return (
        not text[match.end():].strip(" .!?,")
        and rule.resolve(match, today) is not None
    )


def _expand_left(text: str, start: int) -> int:
    """Pull the removal span back over the preposition and article introducing the date,
    so 'due friday' leaves entirely rather than leaving a dangling 'due'."""
    found = _SCHEDULING_PREFIX.search(text[:start])
    return found.start() if found is not None else start


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted(spans)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _tidy(text: str) -> str:
    """Collapse the gaps left by removal and trim the prepositions that outlived them."""
    result = re.sub(r"\s+", " ", text).strip()
    # Removal leaves a hole where the phrase was, and the hole can land next to
    # punctuation: 'the draft, when is it due?' would otherwise become 'the draft , when'.
    result = re.sub(r"\s+([,.;:!?])", r"\1", result)
    while True:
        stripped = _DANGLING_TAIL.sub("", result).strip(" ,")
        if stripped == result:
            return result
        result = stripped


def iso_week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"
