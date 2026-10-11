"""Project aggregate.

Deliberately thin in Phase 1: projects exist so that invariant I1 ("every task belongs
to exactly one project") has something to point at, and so the load-per-week analysis
in M3 has a grouping. Cross-project reporting is not a Phase 1 concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from swayam.events import catalogue as cat
from swayam.events.envelope import RecordedEvent

PROJECT_STREAM_TYPE = "project"


@dataclass(slots=True)
class Project:
    project_id: UUID
    name: str | None = None
    description: str | None = None
    is_default: bool = False
    closed: bool = False
    version: int = 0

    @property
    def exists(self) -> bool:
        return self.name is not None

    def apply(self, recorded: RecordedEvent) -> None:
        payload = recorded.event.payload
        name = recorded.event.type
        if name == cat.PROJECT_CREATED.name:
            self.name = payload["name"]
            self.description = payload.get("description")
            self.is_default = payload.get("is_default", False)
        elif name == cat.PROJECT_CLOSED.name:
            self.closed = True
        # Ignore unknown events (e.g., task events written to the project stream)
        self.version = recorded.seq
