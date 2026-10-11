"""Fixture specification for the eval suite."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Outcome = Literal["captured", "applied", "clarified", "declined", "partial", "smalltalk"]
ClarifiesAbout = Literal[
    "target",
    "required_field",
    "date",
    "low_confidence",
    "unexecutable",
    "none",
]
Category = Literal[
    "mutation_positive",
    "ambiguous_reference",
    "missing_required_field",
    "malformed_date",
    "overconfidence_trap",
]
Forbidden = Literal[
    "mutate_any_task",
    "ask_which_task",
    "invent_blocker",
    "invent_due_date",
    "invent_body",
    "invent_title",
    "guess_target",
    "silent_partial",
]


class SeedTask(BaseModel):
    model_config = {"extra": "forbid"}
    title: str = Field(min_length=1)
    due: str | None = None
    status: Literal["open", "complete"] = "open"
    notes: tuple[str, ...] = ()


class Expectation(BaseModel):
    model_config = {"extra": "forbid"}
    outcome: Outcome
    commits: int
    clarifies_about: ClarifiesAbout = "none"
    required_field: str | None = None
    must_not_do: tuple[Forbidden, ...] = ()
    task_titles: tuple[str, ...] | None = None
    note: str = ""
    known_gap: str = ""


class Case(BaseModel):
    model_config = {"extra": "forbid"}
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    category: Category
    today: str = "2026-09-29"
    seed_tasks: tuple[SeedTask, ...] = ()
    turn: str = Field(min_length=1)
    route_fixture: dict | None = None
    plan_fixture: dict | None = None
    expect: Expectation
    known_gap: str = ""
    tags: tuple[str, ...] = ()
