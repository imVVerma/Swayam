"""Per-turn tracing.

Non-determinism is a debugging problem, so the trace is mandatory infrastructure rather
than an observability nice-to-have. One trace per turn records the route decision, the
retrieved units, the proposed changes, the committed events and the final citations --
which is what makes the "why did you say that" surface in
docs/phase1_arch.md section 13 a free consequence rather than a feature to build.

Utterance text is deliberately excluded from trace payloads. Logs carry identifiers;
raw user text stays in the store where retention rules apply to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class TraceEntry:
    name: str
    data: dict[str, Any]


@dataclass(slots=True)
class Trace:
    turn_id: UUID = field(default_factory=uuid4)
    correlation_id: UUID = field(default_factory=uuid4)
    entries: list[TraceEntry] = field(default_factory=list)

    def record(self, name: str, data: dict[str, Any] | None = None) -> None:
        self.entries.append(TraceEntry(name=name, data=dict(data or {})))

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": str(self.turn_id),
            "correlation_id": str(self.correlation_id),
            "entries": [{"name": e.name, **e.data} for e in self.entries],
        }
