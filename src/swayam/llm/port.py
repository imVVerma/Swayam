"""LLM port.

The port is *structurally typed*: the caller hands over a pydantic schema and gets a
validated instance back. Free-text generation is deliberately not exposed to the
reasoning layer.

That is what makes the mock a genuine substitute for a real provider rather than a
degraded stand-in -- both sides are held to the same output contract, so a scenario
that passes against the mock is testing the pipeline rather than the model. It is also
what makes the mutation planner safe: the plan is a validated object before the domain
ever sees it.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class LLM(Protocol):
    async def complete(
        self,
        *,
        purpose: str,
        instruction: str,
        payload: dict[str, object],
        schema: type[T],
    ) -> T:
        """Return an instance of ``schema``.

        ``purpose`` is a stable key (e.g. 'router.classify', 'mutation.plan') used for
        tracing, cost attribution and mock fixture lookup -- not a prompt to dispatch
        on at runtime.
        """
        ...
