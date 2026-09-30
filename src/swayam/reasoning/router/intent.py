"""Intent taxonomy.

The router's only job is to decide *which latency budget to spend*, not to answer. The
distinction that matters is the fast path (read projections, no model call, sub-300ms)
versus the deep path (retrieve, then synthesise).

``UNCLEAR`` is a first-class outcome, not a failure. The framework's trust-calibration
concern (section 3) applies to behaviour under uncertainty: a system that confidently
misreads "next Tuesday" is worse than one that asks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Intent(StrEnum):
    STATE_QUERY = "state_query"
    """What is true right now. Fast path."""

    TEMPORAL_QUERY = "temporal_query"
    """What I said or planned across time. Fast path, via conversational_claim."""

    LOAD_QUERY = "load_query"
    """Am I overcommitted. Fast path, via commitment_load_week."""

    CROSS_DOMAIN = "cross_domain"
    """Needs retrieval and synthesis. Deep path, asynchronous."""

    MUTATION = "mutation"
    """Change state. Goes to the planner; never straight to the event log."""

    CAPTURE = "capture"
    """Record something without a discrete action verb. Phase 1 routes to the same
    planner as MUTATION; Phase 2 splits feedback out of here."""

    SMALLTALK = "smalltalk"
    UNCLEAR = "unclear"


FAST_PATH_INTENTS = frozenset(
    {Intent.STATE_QUERY, Intent.TEMPORAL_QUERY, Intent.LOAD_QUERY, Intent.SMALLTALK}
)
DEEP_PATH_INTENTS = frozenset({Intent.CROSS_DOMAIN})
MUTATION_INTENTS = frozenset({Intent.MUTATION, Intent.CAPTURE})


@dataclass(frozen=True, slots=True)
class IntentDecision:
    intent: Intent
    confidence: float
    slots: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    @property
    def is_confident(self) -> bool:
        """Below this the router is guessing, and guessing is how trust dies."""
        return self.confidence >= 0.60
