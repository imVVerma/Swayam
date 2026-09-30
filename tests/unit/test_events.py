"""Event envelope and registry: the log's guarantees, in isolation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel

from swayam.core.errors import UnknownEventTypeError, ValidationError
from swayam.events import catalogue as cat
from swayam.events.envelope import Actor, Event
from swayam.events.registry import EventRegistry, EventType


def an_event(type_name: str = "task.created", payload: dict | None = None) -> Event:
    return Event(
        type=type_name,
        payload=payload if payload is not None else {"title": "x", "project_id": str(uuid4())},
        occurred_at=datetime(2026, 9, 29, tzinfo=UTC),
        actor=Actor.user("owner"),
        correlation_id=uuid4(),
    )


class TestRegistry:
    def test_every_phase1_event_name_is_unique(self) -> None:
        names = [t.name for t in cat.PHASE1_EVENT_TYPES]
        assert len(names) == len(set(names))

    def test_health_and_feedback_events_are_absent(self) -> None:
        """The framework defers health to Phase 4 and feedback to Phase 2. Neither may
        be quietly pre-modelled in Phase 1 code."""
        names = set(cat.registry.names())
        assert not any(n.startswith("health.") for n in names)
        assert not any(n.startswith("biometric") for n in names)
        assert not any(n.startswith("feedback.") for n in names)

    def test_unknown_event_type_is_rejected(self) -> None:
        with pytest.raises(UnknownEventTypeError):
            cat.registry.get("task.exploded")

    def test_duplicate_registration_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="already registered"):
            cat.registry.register(cat.TASK_CREATED)

    def test_payload_is_coerced_through_its_schema(self) -> None:
        project_id = uuid4()
        payload = cat.registry.validate(
            "task.created",
            {"title": "  spaced  ", "project_id": str(project_id), "priority": 2},
        )
        assert payload["project_id"] == str(project_id)
        assert payload["priority"] == 2
        # exclude_none keeps stored payloads from accumulating null keys that projections
        # would then have to treat as "explicitly none".
        assert "estimate_minutes" not in payload

    def test_invalid_payload_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"invalid payload for task\.created"):
            cat.registry.validate("task.created", {"title": "no project"})

    def test_missing_required_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            cat.registry.validate("task.created", {"project_id": str(uuid4())})

    def test_blank_title_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            cat.registry.validate(
                "task.created", {"title": "   ", "project_id": str(uuid4())}
            )

    def test_custom_registry_is_isolated(self) -> None:
        class Tiny(BaseModel):
            value: int

        registry = EventRegistry([EventType("tiny.made", Tiny)])
        assert registry.get("tiny.made").payload_model is Tiny
        assert "task.created" not in registry.names()

    def test_due_date_moved_round_trips_iso_dates(self) -> None:
        payload = cat.registry.validate(
            "task.due_date_moved",
            {"from_date": "2026-09-25", "to_date": "2026-10-02", "reason": "slipped"},
        )
        assert date.fromisoformat(payload["to_date"]) == date(2026, 10, 2)


class TestEnvelope:
    def test_event_is_immutable(self) -> None:
        event = an_event()
        with pytest.raises(AttributeError):
            event.type = "task.renamed"  # type: ignore[misc]

    def test_causation_id_is_optional_for_system_events(self) -> None:
        """Invariant I7: system-originated events (reminder firing) have no causing turn."""
        event = Event(
            type="reminder.fired",
            payload={},
            occurred_at=datetime(2026, 9, 29, tzinfo=UTC),
            actor=Actor.system(),
            correlation_id=uuid4(),
        )
        assert event.causation_id is None
        assert event.actor.type.value == "system"

    def test_user_actor_carries_an_id(self) -> None:
        actor = Actor.user("owner")
        assert actor.id == "owner"
        assert actor.type.value == "user"

    def test_each_event_gets_a_distinct_id(self) -> None:
        assert an_event().event_id != an_event().event_id
