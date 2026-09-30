"""Identifier helpers.

Swayam uses a distinct newtype alias per identifier role rather than a bare ``UUID``.
The aliases are structurally identical at runtime, so they carry no enforcement power
on their own -- the value is in the type checker's eyes and in the reader's head. Do
not rely on them for runtime validation.
"""

from typing import NewType
from uuid import UUID, uuid4

EventId = NewType("EventId", UUID)
StreamId = NewType("StreamId", UUID)
ProjectId = NewType("ProjectId", UUID)
TurnId = NewType("TurnId", UUID)
CorrelationId = NewType("CorrelationId", UUID)


def new_id() -> UUID:
    """Generate a fresh v4 UUID.

    Deliberately indirect so the randomness source can be swapped in tests without
    every call site changing.
    """
    return uuid4()
