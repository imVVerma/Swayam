"""Event type registry.

An event type is a name, a payload schema and a version. The registry is the single
place that knows what event names mean, which is what lets the store stay ignorant of
the domain and lets the domain stay ignorant of storage.

Event names are namespaced ``aggregate.verb`` in the past tense. Breaking a payload
means a *new name*, not a mutated one -- the log must remain replayable forever
(docs/phase1_arch.md section 5.4).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from swayam.core.errors import UnknownEventTypeError, ValidationError

TPayload = TypeVar("TPayload", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class EventType:
    name: str
    payload_model: type[BaseModel]
    version: int = 1
    """Increment on additive-only changes. Breaking changes get a new ``name``."""


class EventRegistry:
    def __init__(self, event_types: Iterable[EventType] = ()) -> None:
        self._types: dict[str, EventType] = {}
        for event_type in event_types:
            self.register(event_type)

    def register(self, event_type: EventType) -> None:
        if event_type.name in self._types:
            raise ValueError(f"event type already registered: {event_type.name}")
        self._types[event_type.name] = event_type

    def get(self, name: str) -> EventType:
        try:
            return self._types[name]
        except KeyError:
            raise UnknownEventTypeError(f"unknown event type: {name}") from None

    def names(self) -> list[str]:
        return sorted(self._types)

    def validate(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Coerce a raw payload through its schema, returning a normalised dict.

        Normalisation matters: it means a stored payload always matches the schema it
        was validated against, so projections never see loosely-typed surprises.
        """
        event_type = self.get(name)
        try:
            model = event_type.payload_model.model_validate(payload)
        except PydanticValidationError as exc:
            raise ValidationError(f"invalid payload for {name}: {exc}") from exc
        return model.model_dump(mode="json", exclude_none=True)

    def validate_into(self, name: str, payload: dict[str, Any], schema: type[TPayload]) -> TPayload:
        """Validate and return a *typed* payload, for reducers that want the model
        rather than a dict. The caller names the expected type; the registry checks it
        against what it has registered, so a mismatch is caught here rather than
        surfacing as an AttributeError three layers down."""
        event_type = self.get(name)
        if not issubclass(event_type.payload_model, schema):
            raise ValidationError(
                f"{name} is registered as {event_type.payload_model.__name__}, "
                f"not a {schema.__name__}"
            )
        try:
            return event_type.payload_model.model_validate(payload)
        except PydanticValidationError as exc:
            raise ValidationError(f"invalid payload for {name}: {exc}") from exc
