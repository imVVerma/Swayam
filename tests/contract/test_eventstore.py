"""Event store contract.

One suite, run against every adapter. The Postgres adapter joins by adding a fixture
here and nothing else -- which is the entire payoff of keeping the port narrow.

The properties tested are the ones the event log cannot be allowed to lose: gapless
per-stream ordering, optimistic concurrency, idempotent retries, and payloads that are
normalised on the way in.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import pytest

from swayam.core.clock import FrozenClock
from swayam.core.errors import ConcurrencyError, ValidationError
from swayam.core.ids import CorrelationId
from swayam.events.catalogue import registry
from swayam.events.envelope import Actor, Event
from swayam.storage.eventstore.memory import InMemoryEventStore
from swayam.storage.eventstore.port import EventStore

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
STREAM = uuid5(NAMESPACE_URL, "stream-1")
OTHER = uuid5(NAMESPACE_URL, "stream-2")
CORR = CorrelationId(uuid5(NAMESPACE_URL, "corr-1"))


def a_task_event(title: str = "Send report") -> Event:
    return Event(
        type="task.created",
        payload={"title": title, "project_id": str(uuid5(NAMESPACE_URL, "project"))},
        occurred_at=NOW,
        actor=Actor.user("owner"),
        correlation_id=CORR,
    )


def a_note_event(body: str = "client wants a data pass") -> Event:
    return Event(
        type="task.note_added",
        payload={"body": body},
        occurred_at=NOW,
        actor=Actor.user("owner"),
        correlation_id=CORR,
    )


# Adding an adapter means adding a case here.
ADAPTERS = {"memory": lambda: InMemoryEventStore(clock=FrozenClock(NOW))}


@pytest.fixture(params=sorted(ADAPTERS))
async def store(request: pytest.FixtureRequest) -> AsyncIterator[EventStore]:
    yield ADAPTERS[request.param]()


class TestAppend:
    async def test_first_append_lands_at_seq_one(self, store: EventStore) -> None:
        result = await store.append(
            stream_id=STREAM, stream_type="task", expected_version=0,
            events=(a_task_event(),), correlation_id=CORR,
        )
        assert result.new_version == 1
        assert result.previous_version == 0
        assert result.events[0].seq == 1

    async def test_sequences_are_gapless_and_ascending(self, store: EventStore) -> None:
        for _ in range(5):
            await store.append(
                stream_id=STREAM, stream_type="task",
                expected_version=await store.stream_version(STREAM),
                events=(a_note_event(),), correlation_id=CORR,
            )
        events = await store.read_stream(STREAM)
        assert [e.seq for e in events] == [1, 2, 3, 4, 5]

    async def test_a_batch_gets_consecutive_seqs(self, store: EventStore) -> None:
        result = await store.append(
            stream_id=STREAM, stream_type="task", expected_version=0,
            events=(a_task_event(), a_note_event(), a_note_event("second")),
            correlation_id=CORR,
        )
        assert [e.seq for e in result.events] == [1, 2, 3]

    async def test_streams_are_independent(self, store: EventStore) -> None:
        await store.append(
            stream_id=STREAM, stream_type="task", expected_version=0,
            events=(a_task_event(),), correlation_id=CORR,
        )
        await store.append(
            stream_id=OTHER, stream_type="task", expected_version=0,
            events=(a_task_event("other"),), correlation_id=CORR,
        )
        assert await store.stream_version(STREAM) == 1
        assert await store.stream_version(OTHER) == 1
        assert len(await store.read_stream(STREAM)) == 1

    async def test_empty_batch_is_refused(self, store: EventStore) -> None:
        with pytest.raises(ValidationError):
            await store.append(
                stream_id=STREAM, stream_type="task", expected_version=0,
                events=(), correlation_id=CORR,
            )

    async def test_unknown_stream_has_version_zero(self, store: EventStore) -> None:
        assert await store.stream_version(uuid5(NAMESPACE_URL, "never-written")) == 0
        assert await store.read_stream(uuid5(NAMESPACE_URL, "never-written")) == ()

    async def test_payload_is_normalised_on_write(self, store: EventStore) -> None:
        """Stored payloads always match the schema they were validated against, so a
        projection never meets a loosely-typed surprise."""
        await store.append(
            stream_id=STREAM, stream_type="task", expected_version=0,
            events=(a_task_event(),), correlation_id=CORR,
        )
        stored = (await store.read_stream(STREAM))[0]
        assert stored.event.payload == registry.validate(
            "task.created", stored.event.payload
        )
        assert "estimate_minutes" not in stored.event.payload

    async def test_invalid_payload_is_refused_before_write(self, store: EventStore) -> None:
        bad = Event(
            type="task.created", payload={"title": "no project"}, occurred_at=NOW,
            actor=Actor.user("owner"), correlation_id=CORR,
        )
        with pytest.raises(ValidationError):
            await store.append(
                stream_id=STREAM, stream_type="task", expected_version=0,
                events=(bad,), correlation_id=CORR,
            )
        assert await store.stream_version(STREAM) == 0


class TestConcurrency:
    async def test_stale_expected_version_is_refused(self, store: EventStore) -> None:
        await store.append(
            stream_id=STREAM, stream_type="task", expected_version=0,
            events=(a_task_event(),), correlation_id=CORR,
        )
        with pytest.raises(ConcurrencyError, match="is at version 1"):
            await store.append(
                stream_id=STREAM, stream_type="task", expected_version=0,
                events=(a_note_event(),), correlation_id=CORR,
            )

    async def test_a_refused_append_writes_nothing(self, store: EventStore) -> None:
        """The failure mode this guards against is a half-applied batch."""
        await store.append(
            stream_id=STREAM, stream_type="task", expected_version=0,
            events=(a_task_event(),), correlation_id=CORR,
        )
        with pytest.raises(ConcurrencyError):
            await store.append(
                stream_id=STREAM, stream_type="task", expected_version=0,
                events=(a_note_event("one"), a_note_event("two")), correlation_id=CORR,
            )
        assert len(await store.read_stream(STREAM)) == 1

    async def test_concurrent_appends_do_not_interleave(self, store: EventStore) -> None:
        import asyncio

        async def append(i: int) -> None:
            while True:
                version = await store.stream_version(STREAM)
                try:
                    await store.append(
                        stream_id=STREAM, stream_type="task", expected_version=version,
                        events=(a_note_event(f"note {i}"),), correlation_id=CORR,
                    )
                    return
                except ConcurrencyError:
                    continue

        await asyncio.gather(*(append(i) for i in range(10)))
        events = await store.read_stream(STREAM)
        assert [e.seq for e in events] == list(range(1, 11))
        assert len({e.seq for e in events}) == 10


class TestIdempotency:
    async def test_a_retried_mutation_does_not_duplicate(self, store: EventStore) -> None:
        """A conversational system retries. Duplicate appends are not hypothetical."""
        first = await store.append(
            stream_id=STREAM, stream_type="task", expected_version=0,
            events=(a_task_event(),), correlation_id=CORR, idempotency_key="turn-1",
        )
        second = await store.append(
            stream_id=STREAM, stream_type="task", expected_version=0,
            events=(a_task_event(),), correlation_id=CORR, idempotency_key="turn-1",
        )
        assert second.deduplicated
        assert len(await store.read_stream(STREAM)) == 1
        assert second.events[0].event.event_id == first.events[0].event.event_id

    async def test_idempotency_is_scoped_to_a_correlation(self, store: EventStore) -> None:
        await store.append(
            stream_id=STREAM, stream_type="task", expected_version=0,
            events=(a_task_event(),), correlation_id=CORR, idempotency_key="k",
        )
        result = await store.append(
            stream_id=STREAM, stream_type="task", expected_version=1,
            events=(a_note_event(),),
            correlation_id=CorrelationId(uuid5(NAMESPACE_URL, "corr-2")),
            idempotency_key="k",
        )
        assert not result.deduplicated
        assert len(await store.read_stream(STREAM)) == 2

    async def test_a_retry_without_a_key_is_not_deduplicated(self, store: EventStore) -> None:
        """No key means the caller has opted out of retry safety, not that retries
        are impossible -- so this must be a plain second append."""
        for _ in range(2):
            await store.append(
                stream_id=STREAM, stream_type="task",
                expected_version=await store.stream_version(STREAM),
                events=(a_task_event(),), correlation_id=CORR,
            )
        assert len(await store.read_stream(STREAM)) == 2


class TestReadAll:
    async def test_returns_the_log_in_append_order_across_streams(self, store: EventStore) -> None:
        await store.append(
            stream_id=STREAM, stream_type="task", expected_version=0,
            events=(a_task_event(),), correlation_id=CORR,
        )
        await store.append(
            stream_id=OTHER, stream_type="task", expected_version=0,
            events=(a_task_event("other"),), correlation_id=CORR,
        )
        page = await store.read_all()
        assert [e.stream_id for e in page.events] == [STREAM, OTHER]

    async def test_cursor_resumes_without_gaps_or_repeats(self, store: EventStore) -> None:
        for i in range(7):
            await store.append(
                stream_id=STREAM, stream_type="task",
                expected_version=await store.stream_version(STREAM),
                events=(a_note_event(f"note {i}"),), correlation_id=CORR,
            )
        seen: list[int] = []
        cursor = None
        while True:
            page = await store.read_all(after_cursor=cursor, limit=3)
            seen.extend(e.seq for e in page.events)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        assert seen == [1, 2, 3, 4, 5, 6, 7]

    async def test_pagination_terminates_on_an_exact_multiple(self, store: EventStore) -> None:
        """Off-by-one here silently drops the last event of every full page."""
        for i in range(4):
            await store.append(
                stream_id=STREAM, stream_type="task",
                expected_version=await store.stream_version(STREAM),
                events=(a_note_event(f"n{i}"),), correlation_id=CORR,
            )
        page = await store.read_all(limit=4)
        assert len(page.events) == 4
        assert page.next_cursor is None

    async def test_empty_log_yields_no_cursor(self, store: EventStore) -> None:
        page = await store.read_all()
        assert page.events == ()
        assert page.next_cursor is None
