"""Deterministic mock LLM.

Phase 1 ships against this, not a live model. Reproducible tests, zero cost, and no
vendor coupling before the reasoning contract has proved itself.

Variance is opt-in: pass a ``seed`` to enable jitter on categorical choices, so
robustness work (clarification thresholds, ambiguity handling) can be measured without
flaky tests.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from swayam.core.errors import ValidationError as SwayamValidationError
from swayam.observability.trace import Trace

T = TypeVar("T", bound=BaseModel)

Fixture = BaseModel | dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]


class MockLLM:
    def __init__(
        self,
        fixtures: dict[str, Fixture] | None = None,
        *,
        seed: int | None = None,
        trace: Trace | None = None,
    ) -> None:
        self._fixtures = dict(fixtures or {})
        self._rng = random.Random(seed) if seed is not None else None
        self._trace = trace
        self.calls: list[tuple[str, str]] = []

    def register(self, purpose: str, fixture: Fixture) -> None:
        self._fixtures[purpose] = fixture

    async def complete(
        self,
        *,
        purpose: str,
        instruction: str,
        payload: dict[str, object],
        schema: type[T],
    ) -> T:
        self.calls.append((purpose, instruction))
        if self._trace is not None:
            self._trace.record(f"llm.{purpose}", {"keys": sorted(payload)})

        fixture = self._fixtures.get(purpose)
        if fixture is None:
            raise LookupError(
                f"no mock fixture for purpose {purpose!r}; "
                "register one or use a real provider adapter"
            )

        if callable(fixture):
            raw = fixture(payload)
        elif isinstance(fixture, BaseModel):
            raw = fixture.model_dump(mode="json")
        else:
            raw = dict(fixture)

        if self._rng is not None and "alternatives" in raw:
            alternatives = raw.pop("alternatives")
            raw = dict(self._rng.choice(alternatives))

        try:
            return schema.model_validate(raw)
        except ValidationError as exc:
            raise SwayamValidationError(
                f"mock response for {purpose!r} did not satisfy {schema.__name__}: {exc}"
            ) from exc
