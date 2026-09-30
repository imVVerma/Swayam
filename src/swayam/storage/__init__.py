"""Storage: event log, projections, semantic store. Nothing above this layer knows
which adapter it is talking to."""

from swayam.storage.bootstrap import Storage, build_storage
from swayam.storage.repositories import (
    DEFAULT_PROJECT_ID,
    ProjectRepository,
    TaskRepository,
)

__all__ = [
    "DEFAULT_PROJECT_ID",
    "ProjectRepository",
    "Storage",
    "TaskRepository",
    "build_storage",
]
