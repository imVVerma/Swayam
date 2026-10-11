"""Application composition root.

Assembles a runnable Swayam from the ports and the concrete adapters chosen for Phase 1:
file-backed event log, deterministic router, rule-based planner, no live model.

The reason this is a single function rather than a DI container is that the wiring *is*
the architectural claim. If the graph in docs/phase1_arch.md section 9 is wrong, it is
wrong in one readable place.

``reasoning="llm"`` swaps in ``LLMRouter`` and ``LLMPlanner``. Both implement the same
protocols as the deterministic pair, which is what makes this a one-word change here and
nothing at all anywhere downstream. The router is given the deterministic one as its
fallback, so a provider that is unavailable degrades to "asks the user" rather than to a
wrong answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from swayam.conversation.turn import TurnOrchestrator
from swayam.core.clock import Clock, ISTClock
from swayam.core.ids import CorrelationId
from swayam.events.envelope import Actor
from swayam.llm.mock import MockLLM
from swayam.observability.trace import Trace
from swayam.reasoning.mutation.llm_planner import LLMPlanner
from swayam.reasoning.mutation.rule_planner import MutationPlanner, RuleBasedPlanner
from swayam.reasoning.router.deterministic import DeterministicRouter, IntentRouter
from swayam.reasoning.router.llm_router import LLMRouter
from swayam.storage.bootstrap import Storage, build_storage
from swayam.storage.projections.query import QueryProjection
from swayam.storage.repositories import ProjectRepository

Reasoning = Literal["deterministic", "llm"]


@dataclass(frozen=True, slots=True)
class Runtime:
    storage: Storage
    projection: QueryProjection
    orchestrator: TurnOrchestrator
    llm: MockLLM
    clock: Clock


async def build_runtime(
    *,
    clock: Clock | None = None,
    user_id: str = "owner",
    reasoning: Reasoning = "deterministic",
    llm: MockLLM | None = None,
    path: Path | None = None,
) -> Runtime:
    """Build a runtime with the default project already bootstrapped.

    If ``path`` is provided, uses a file-backed event store at that location.
    Otherwise uses an in-memory store (for tests and scenarios).
    """
    storage = build_storage(clock=clock or ISTClock(), path=path)
    projection = QueryProjection()
    _llm = llm or MockLLM(trace=Trace())

    deterministic_router = DeterministicRouter()
    router: IntentRouter
    planner: MutationPlanner
    if reasoning == "llm":
        router = LLMRouter(_llm, fallback=deterministic_router)
        planner = LLMPlanner(_llm, fallback=RuleBasedPlanner())
    else:
        router = deterministic_router
        planner = RuleBasedPlanner()

    orchestrator = TurnOrchestrator(
        store=storage.events,
        clock=storage.clock,
        router=router,
        planner=planner,
        projection=projection,
        user_id=user_id,
    )

    # The default project is what uncategorised tasks attach to (invariant I1), so it
    # has to exist before the first turn rather than being created lazily mid-flight.
    await ProjectRepository(storage.events, storage.clock).ensure_default(
        actor=Actor.user(user_id), correlation_id=CorrelationId(Trace().correlation_id)
    )
    page = await storage.events.read_all()
    projection.rebuild(page.events)

    return Runtime(
        storage=storage,
        projection=projection,
        orchestrator=orchestrator,
        llm=_llm,
        clock=storage.clock,
    )

