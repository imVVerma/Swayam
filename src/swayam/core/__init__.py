"""Foundational primitives: identifiers, clocks, errors.

Nothing in this package may import from any other Swayam module. It is the root of
the dependency graph (see docs/phase1_arch.md section 9).
"""

from swayam.core.clock import Clock, FrozenClock, SystemClock
from swayam.core.errors import (
    ConcurrencyError,
    DomainError,
    InvariantViolation,
    SwayamError,
    UnknownEventTypeError,
    ValidationError,
)
from swayam.core.ids import CorrelationId, EventId, ProjectId, StreamId, TurnId, new_id

__all__ = [
    "Clock",
    "ConcurrencyError",
    "CorrelationId",
    "DomainError",
    "EventId",
    "FrozenClock",
    "InvariantViolation",
    "ProjectId",
    "StreamId",
    "SwayamError",
    "SystemClock",
    "TurnId",
    "UnknownEventTypeError",
    "ValidationError",
    "new_id",
]
