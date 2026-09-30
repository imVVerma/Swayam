"""Reply composition with provenance.

Every reply carries citations to the state that produced it (docs/phase1_arch.md
section 7.4). An ungrounded claim is a bug, not a style issue -- the eval suite scores
citation validity, and a claim that cannot be traced to a task or an event fails it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Citation:
    kind: str
    """'task', 'event' or 'memory'."""
    ref: str
    """Human-readable handle shown to the user, e.g. "Grant draft"."""
    event_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    citations: tuple[Citation, ...] = ()
    clarification: str | None = None
    """Set when the system is asking rather than answering. Asking is a first-class
    outcome, not a failure state."""

    @property
    def is_clarification(self) -> bool:
        return self.clarification is not None

    def with_citation(self, citation: Citation) -> Reply:
        return Reply(self.text, (*self.citations, citation), self.clarification)


def format_citations(citations: tuple[Citation, ...]) -> str:
    if not citations:
        return ""
    handles = ", ".join(f"{c.ref}" for c in citations)
    return f"  ({handles})"
