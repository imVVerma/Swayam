"""Error hierarchy.

One base class, ``SwayamError``, so the interface layer can distinguish "Swayam said no"
from "Swayam broke" without string matching.
"""

from __future__ import annotations


class SwayamError(Exception):
    """Root of every error Swayam raises deliberately."""


class DomainError(SwayamError):
    """A command was refused because the aggregate would violate a rule."""


class InvariantViolation(DomainError):
    """A hard invariant (I1-I7 in docs/phase1_arch.md section 4.3) was breached."""


class ValidationError(SwayamError):
    """Input failed structural or type validation before reaching the domain."""


class ConcurrencyError(SwayamError):
    """Optimistic concurrency failed: the stream moved under us.

    Expected in normal operation. A conversational system retries, so two writers
    racing for the same task is routine, not exceptional -- but it must never be
    silently absorbed into a duplicate append.
    """


class UnknownEventTypeError(SwayamError):
    """An event name was used that is not in the registry."""
