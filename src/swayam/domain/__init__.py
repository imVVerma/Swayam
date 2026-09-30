"""Domain: aggregates, commands, invariants. Pure -- no database, no clock, no I/O.

Repositories deliberately do not live here; they bridge to the event log and belong in
``swayam.storage``. See that module's docstring and ``tests/arch/``.
"""

from swayam.domain.project import PROJECT_STREAM_TYPE, Project
from swayam.domain.task import (
    ALLOWED_TRANSITIONS,
    TASK_STREAM_TYPE,
    AddNote,
    ArchiveTask,
    ChangeEstimate,
    ChangePriority,
    ChangeStatus,
    Command,
    CommandContext,
    CreateTask,
    LinkToEvent,
    RenameTask,
    ScheduleTask,
    Task,
    TaskStatus,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "PROJECT_STREAM_TYPE",
    "TASK_STREAM_TYPE",
    "AddNote",
    "ArchiveTask",
    "ChangeEstimate",
    "ChangePriority",
    "ChangeStatus",
    "Command",
    "CommandContext",
    "CreateTask",
    "LinkToEvent",
    "Project",
    "RenameTask",
    "ScheduleTask",
    "Task",
    "TaskStatus",
]
