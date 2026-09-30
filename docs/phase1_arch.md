# Swayam — Phase 1 Technical Design Document

**Phase:** 1 — Foundation (Temporal Awareness + Project/Task Tracking, conversational text)
**Status:** Draft v0.1 — pending stack ratification
**Source of truth:** `Personal Intelligence Platform_ Strategic Framework.html`
**Explicitly out of scope:** Health & Wellness, Voice, Feedback capture, proactive insights

---

## 1. Purpose

This document specifies the Phase 1 architecture for Swayam: the event-sourced core, the
Temporal + Task domain model, the read-model layer, the two-tier reasoning architecture, and
the conversational mock runtime that lets us validate the product thesis with zero external
dependencies.

Phase 1 exists to answer one question from §10 of the framework:

> *"What is the minimum viable cross-domain insight that proves the 'digital self' thesis
> without requiring health data?"*

**Answer: commitment reconciliation.** The system can, using only temporal + task data, answer:

- *"What did I say I'd do this week?"* (§2 — Tasks as conversation anchor)
- *"How did last week go?"* (§8 — sample dialogue, trimmed of the feedback clause)
- *"Am I overcommitted this month?"* (§8 — load analysis, no health signal required)

If those three hold up in conversational use, the thesis is validated with the lowest-risk
data available. Everything else in the framework is an extension of the same machinery.

---

## 2. Core constraints extracted from the framework

| # | Constraint | Source | Phase 1 obligation |
|---|---|---|---|
| C1 | The conversational layer *is* the product; modules are data sources, not apps | §1, §2 | There is no task UI that isn't downstream of the chat turn. All writes originate from natural language or are reconciled to it. |
| C2 | Unified query layer required before the AI layer can reason across domains | §3 | A `retrieval` layer sits between storage and reasoning. It is the only component permitted to touch more than one store. |
| C3 | Latency vs. depth → tiered architecture: fast intent-router + async deep reasoning | §3, §6.1 | Every turn is routed. The route decision (not model choice) determines latency budget. |
| C4 | Event-sourced core; state is a derived view | §4.2 | The event log is the only source of truth. No component may write a projection directly. |
| C5 | Layered storage: structured separated from semantic, joined at retrieval time | §4.1 | Two storage *domains* (structured, semantic) with distinct schemas and distinct encryption keys. Joined only in `retrieval`. |
| C6 | Health data isolation — never merged into a general-purpose data lake | §4.3 | The health store is a *reserved boundary* in Phase 1: a separate database, separate key, query-time-only join. An architecture test enforces that no Phase 1 code path can reach it. |
| C7 | Per-user encryption keys | §4.4 | Envelope encryption from the first migration. Never a shared system key. |
| C8 | Memory drift and staleness — reconcile "what I told it in March" with "what's true now" | §3 | Present-tense assertions resolve **only** against projections. See §8.7. |
| C9 | Graceful degradation — a reasoning-layer outage must not take data access down | §3 | The conversational read path and the event store are independently deployable/failable. The chat surface degrades to read-only mode. |
| C10 | Grounded recall, not hallucinated recall | §6.2 | Every answer carries citations to the events/rows that produced it. Ungrounded answers are a test failure, not a style issue. |
| C11 | Cross-domain output is *observation*, not claim | §6.3 | Load/overcommitment analysis is phrased as observation. Reinforced by prompt policy and eval. |
| C12 | Data portability + deletion guarantees | §5 | Export and hard-delete are implemented and tested in Phase 1, before any sensitive data exists. Proving deletion on low-risk data is how the guarantee becomes credible. |

### Phase-ordering constraints (from §7)

- Health and Voice are **deferred**, not merely unbuilt. No schemas, no columns, no interfaces
  that presume them. Deferred ≠ anticipated-in-code.
- The reasoning backend must be stable *before* voice is layered on (Phase 3 rationale). This is
  why the reasoning contract in §8 is defined as a narrow interface rather than a UI-coupled one.

---

## 3. Architecture overview

```
                    ┌──────────────────────────────────────────┐
   text utterance   │            conversation/turn             │
   ───────────────► │  session · turn-id · trace-id            │
                    └───────────────────┬──────────────────────┘
                                        │
                    ┌───────────────────▼──────────────────────┐
                    │         reasoning/router (tier 0)        │
                    │  deterministic classifier → intent+slots │
                    │  budget: <150ms · no history fan-out    │
                    └───────┬───────────────────────┬──────────┘
              STATE_QUERY   │                       │ MUTATION
              CROSS_DOMAIN  │                       │
                            ▼                       ▼
        ┌───────────────────────────┐   ┌────────────────────────────┐
        │ reasoning/fast (tier 1)   │   │ reasoning/mutation_planner│
        │ read models only, no LLM  │   │ emit ProposedChange[]     │
        └─────────────┬─────────────┘   └─────────────┬──────────────┘
                      │                               │ validate
                      │                               ▼
                      │                    ┌─────────────────────┐
                      │                    │  domain/  invariants │
                      │                    └──────────┬──────────┘
                      │                               │ commit
                      │                               ▼
                      │                    ┌─────────────────────┐
                      │                    │  storage/eventstore │
                      │                    │  append-only, seq   │
                      │                    └──────────┬──────────┘
                      │                               │
                      │        ┌──────────────────────▼──────┐
                      │        │  storage/projections (L1)   │
                      │        │  task_current, agenda_day │
                      │        └──────────────┬──────────────┘
                      │                       │
        ┌─────────────▼───────────┐   ┌───────▼───────────────┐
        │ reasoning/deep (tier 2) │   │  storage/semantic (L2) │
        │ retrieval → synthesize  │   │  pgvector · own key    │
        └─────────────┬───────────┘   └───────┬───────────────┘
                      └───────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │  retrieval/  (the join    │  ← only component
                    │  C2: sole cross-store     │    permitted to
                    │  reader                    │    touch both
                    └───────────────────────────┘

  ── reserved, NOT built in Phase 1 ──
  health store: separate DB, separate key, query-time join only (C6)
```

---

## 4. Domain model

### 4.1 Aggregate roots

| Aggregate | Purpose | Key state |
|---|---|---|
| `Task` | A unit of committed work | title, status, project, due date, priority, estimate |
| `Project` | A named grouping of tasks | name, description, status, default horizon |
| `Commitment` (temporal) | A dated promise: "X will be done by D" | subject, kind, due date, status |
| `Event` (temporal) | A dated occurrence on the calendar | title, window, location, kind |
| `Reminder` | A notification to be raised | trigger time, subject, fired state |

`Commitment` is the load-bearing concept and is called out separately because it is what makes
temporal and task domains *joinable* in Phase 1. A task with a due date is itself a commitment.
An event ("client review, Thursday") is a commitment to be *present*. Overcommitment analysis
in §8 is arithmetic over commitments per week.

### 4.2 Task lifecycle

```
                 ┌──────────┐
      create ────│  OPEN    │◀──── reopen
                 └────┬─────┘
        due set / move │
                 ┌────▼─────┐
                 │  BLOCKED │  blocked_by: task | event | external
                 └────┬─────┘
        unblock     │
                 ┌────▼─────┐
                 │ COMPLETE │──── (terminal in Phase 1; reopen allowed)
                 └──────────┘
```

Statuses: `OPEN`, `BLOCKED`, `COMPLETE`. No `CANCELLED` in Phase 1 — a cancelled task is a
`task.closed` with a reason, which keeps the event vocabulary small. Revisit when the
framework's "forward-looking" requirement forces explicit shelving.

### 4.3 Invariants (enforced in `domain/`, tested in isolation, no I/O)

- I1. Every `Task` belongs to exactly one `Project` (a `default` project exists from bootstrap).
- I2. A `Task` may have at most one `due_date`; setting it twice is a *move*, never an overwrite.
- I3. `due_date` cannot be set in the past without explicit `allow_backdate` on the command.
- I4. `Task` transitions are legal per §4.2; illegal transitions raise, never silently no-op.
- I5. An aggregate rejects an event whose `expected_version` ≠ current version (optimistic
  concurrency — the only defence against double-append from a retried LLM tool call).
- I6. No event is ever deleted. A `stream.voided` marker exists for legal erasure (§12) but the
  payload is tombstoned, not removed, until the retention window expires.
- I7. Every mutation event carries a non-null `causation_id` pointing at the conversation turn
  that produced it. Events not produced by a turn (e.g. reminder auto-fire) carry a `system`
  actor and a null causation.

**I5 deserves emphasis.** A conversational system retries. The duplicate-append failure mode is
not theoretical, and the event log is the thing you can least afford to corrupt. Idempotency is
handled twice: `idempotency_key` at the store boundary, `expected_version` in the domain.

---

## 5. Event log design

### 5.1 Envelope

Every row is self-describing. The store is append-only.

```sql
CREATE TABLE events (
    event_id          uuid PRIMARY KEY,
    stream_id         uuid NOT NULL,
    stream_type       text NOT NULL,
    seq               bigint NOT NULL,            -- per-stream, gapless, starts at 1
    event_type        text NOT NULL,
    event_version     int  NOT NULL,              -- payload schema version
    occurred_at       timestamptz NOT NULL,       -- when it happened in the world
    recorded_at       timestamptz NOT NULL,       -- when we learned of it
    actor_type        text NOT NULL,              -- 'user' | 'system' | 'import'
    actor_id          text,
    causation_id      uuid,                       -- conversation turn that caused this
    correlation_id    uuid NOT NULL,              -- a whole user-facing action
    idempotency_key   text,
    payload           jsonb NOT NULL,
    UNIQUE (stream_id, seq)
);
```

Design notes, each tied to a constraint:

- `occurred_at` vs `recorded_at` — the framework's "historical and forward-looking" requirement
  (§4.2). A deadline moved on 3 Sept for the 20th is an event recorded on the 3rd. Without two
  clocks, forward-looking queries are impossible to express.
- `causation_id` — the traceability spine for C10. Every state change is explainable back to a
  sentence the user actually typed, and a "what did you just change and why" surface falls out
  for free.
- `payload jsonb` + `event_version` — evolution without migrations (§5.4).
- No `updated_at` on event rows. There is nothing to update. (Stated explicitly because it will
  otherwise be added by someone in month three.)

### 5.2 Event catalogue (Phase 1)

| Event | Emitted when | Notable payload |
|---|---|---|
| `task.created` | new task | `title, project_id, priority, estimate_min` |
| `task.renamed` | title change | `from, to` |
| `task.status_changed` | legal transition | `from, to, reason` |
| `task.due_date_set` | first due date | `due_date` |
| `task.due_date_moved` | subsequent change | `from, to, reason` |
| `task.priority_changed` | — | `from, to` |
| `task.estimated` | estimate change | `from_min, to_min` |
| `task.linked_to_event` | tied to a calendar event | `event_id` |
| `task.note_added` | free text note | `body` |
| `task.archived` | soft delete | `reason` |
| `project.created` | bootstrap or new project | `name` |
| `event.created` | calendar entry | `title, starts_at, ends_at, kind` |
| `event.rescheduled` | time change | `from, to` |
| `event.cancelled` | — | `reason` |
| `reminder.set` | — | `fire_at, subject_stream_id` |
| `reminder.fired` | system, on trigger | `reminder_id` |
| `reminder.snoozed` | user | `from, to` |
| `stream.voided` | erasure request | `scope, tombstone_hash` |

`task.note_added` and `event.*` are the *seams* for Phase 2. Feedback capture (§7 Phase 2) will
add `feedback.*` events and a `Feedback` aggregate; it must not require a change to this table,
the envelope, or the projection contract. It will not.

### 5.3 Ordering, concurrency, idempotency

- **Per-stream ordering** via `seq`, allocated under a row lock on the stream. No global
  sequence — a global counter is a throughput ceiling for no benefit at this scale.
- **Cross-stream ordering** is *not* guaranteed and must never be assumed. Anything needing a
  total order reads the event's `recorded_at` and tolerates ties.
- **Idempotency**: `(correlation_id, idempotency_key)` is unique. A retried mutation returns the
  original events rather than appending duplicates.
- **Projections** consume in `recorded_at, event_id` order and checkpoint their offset.

### 5.4 Schema evolution

- Payloads are additive-only within an `event_version`.
- Breaking changes get a new `event_type` (e.g. `task.due_date_set` → `task.due_scheduled`).
- Old payloads are never rewritten. Projections handle both versions. The store keeps the
  historical truth of what the system believed at the time.

---

## 6. Storage architecture

### 6.1 Layer 0 — event store

Postgres. Append-only, enforced by revoking `UPDATE`/`DELETE` from the application role.
Rebuild everything else from here; treat this as the only irreplaceable asset.

### 6.2 Layer 1 — read models (projections)

| Projection | Grain | Serves |
|---|---|---|
| `task_current` | one row per live task | fast path, agenda strip, mutation validation |
| `agenda_day` | task/event per calendar day | "what's today", "how did last week go" |
| `commitment_load_week` | commitments per ISO week | "am I overcommitted this month" |
| `task_timeline` | per-task event history | "what happened to X" |
| `conversational_claim` | past stated commitments, keyed to stream | staleness reconciliation (§8.7) |

Projections are rebuildable, disposable, and must be trivially correct before they are fast.
The first projection (`task_current`) is written alongside a test that replays the entire event
log from empty and asserts convergence — the standard projection fitness function.

### 6.3 Layer 2 — semantic store

Embeddings over: task titles/notes, event titles, and conversation turns (with retention). Behind
a `SemanticStore` port so the engine is swappable. Phase 1 default is `pgvector` in a separate
Postgres *schema* with a **separate encryption key** — satisfying C5's separation of storage
domains without paying for a second distributed system before we know we need one.

> **This is the framework's "layered storage" requirement, interpreted as logical separation of
> storage *domains* and encryption boundaries, not a mandate for one engine per layer.** If
> vector search later needs a dedicated engine, the port absorbs it. Flagging it explicitly
> because it is the one place Phase 1 deliberately under-builds for simplicity, and it is the
> decision most worth revisiting at the Phase 2 boundary.

### 6.4 Reserved Layer 3 — health store (NOT BUILT)

Exists in this document as a *constraint*, not a component. When Phase 4 arrives: separate
database, separate key, separate credentials, joined only at query time in `retrieval`, never
written by any Phase 1 code path. An architecture fitness test (§13) fails the build if any
non-health module imports a health module. The point is that the "one big data lake" mistake
cannot be made *later* by accident.

### 6.5 Retention and deletion

- Conversation turns: retained by default, TTL configurable, raw audio never exists in Phase 1.
- Export: full-fidelity JSON of the entire event log + derived views, per user, on demand.
- Erasure: `stream.voided` tombstones payload, cascades to projections and embeddings, and is
  exercised by a test. Proven on Phase 1 data so the guarantee is real before health data exists.

---

## 7. Reasoning architecture

### 7.1 Two tiers

| | Tier 0 — router | Tier 1 — fast path | Tier 2 — deep path |
|---|---|---|---|
| Function | classify the utterance | answer from structure | answer from meaning |
| Data | utterance only | projections (Layer 1) | Layer 1 + Layer 2 via `retrieval` |
| Model | small / rules | none | LLM |
| Budget | <150 ms | <300 ms total | async, 2–10 s |
| Failure mode | fall through to clarify | answer "I don't have that" | degraded/retried |

The router is a **separate, cheap, deterministic-first** step. Its job is to decide which of the
three budgets to spend, not to answer. Replacing it with a small model must not change the shape
of the system.

### 7.2 Intent taxonomy (Phase 1)

| Intent | Example | Path |
|---|---|---|
| `STATE_QUERY` | "what's on today?" | fast |
| `TEMPORAL_QUERY` | "what did I say I'd do this week?" | fast, via `conversational_claim` |
| `LOAD_QUERY` | "am I overcommitted this month?" | fast, via `commitment_load_week` |
| `MUTATION` | "push the grant draft to Thursday" | mutation planner |
| `CAPTURE` | "note that the client wants a data pass" | mutation planner (→ Phase 2 feedback seam) |
| `CROSS_DOMAIN` | "how did last week go?" | deep |
| `SMALLTALK` | "hey" | trivial |
| `UNCLEAR` | "fix it" | clarify |

Low-confidence classification never guesses. It routes to `UNCLEAR` and asks. §3's
trust-calibration concern applies to *the system's behaviour under uncertainty*, not just to data
consent — a system that confidently misinterprets "next Tuesday" is worse than one that asks.

### 7.3 The mutation safety pipeline

**The LLM never writes to the event log.** This is the single most important safety property in
Phase 1.

```
utterance
  → router (intent + slots)
  → MutationPlanner  (LLM)  ──emits──►  ProposedChange[]   (typed, no side effects)
  → domain/          (pure)  ──validates─► Accept | Reject(reason)
  → events/          ──appends──►  events (with causation_id = turn_id)
  → projections      ──rebuild──►  task_current
  → composer         ──confirms─►  "Moved 'Grant draft' to Thursday."
```

`ProposedChange` is a data structure, not a function call. It can be logged, diffed, asserted
against, shown to the user for confirmation in high-risk cases, and unit-tested without a
database. A malformed plan is a validation failure, not a corrupted aggregate.

### 7.4 Grounding and citations (C10)

Every composed reply carries provenance. For a `CROSS_DOMAIN` answer, the reply is assembled
from retrieved units, each retaining its source event/row ids, which are rendered as
human-readable inline citations. The eval suite (§13) measures *citation validity* — the rate at
which every claim in an answer maps to a retrieved fact. An ungrounded claim fails the eval.

### 7.5 Graceful degradation (C9)

Tier 0 and Tier 1 are read-only and survive a Tier 2 outage. If the LLM provider is unreachable,
the system keeps accepting mutations through the planner's validated path and tells the user that
cross-domain reasoning is temporarily unavailable. Data access is never gated on model access.

### 7.6 Policy constraints in the system prompt

- Present-tense facts resolve against projections only (§8.7).
- Load/overcommitment framing is observational: "you have 11 deadlines, three more than last
  month" — never "you are overcommitted" as a verdict (C11).
- The system does not give advice in Phase 1. It reports state and asks.
- Refuse and re-ask on ambiguous dates, entity references, and destructive operations.

---

## 8. Memory drift and staleness (C8)

The framework flags this explicitly, and it is the most likely place a naive implementation
produces confidently wrong answers.

**Rule: events are the only source of *history*; projections are the only source of *present
tense*.**

Mechanics:

1. A past utterance ("I said I'd finish the report Friday") is stored as a
   `conversational_claim` — an immutable record of a *belief held at the time*.
2. When that claim is retrieved, it is joined against `task_current`.
3. If the claim has been superseded, the system says so explicitly rather than repeating the
   stale assertion:
   > *"In March you said the report was due Friday. It was moved to the 12th and completed on
   > the 10th."*
4. Beliefs are never rewritten. The record of what the user once thought is itself part of the
   digital self.

---

## 9. Repository structure

Modular monolith. Explicit layering enforced by a fitness test rather than by discipline.

```
swayam/
├── docs/
│   ├── phase1_arch.md            # this document
│   ├── adr/                      # one file per irreversible decision
│   └── glossary.md               # domain vocabulary, single source
├── src/swayam/
│   ├── core/                     # ids, clock, result types, errors, idempotency
│   ├── domain/                   # aggregates, state machines, invariants — pure, no I/O
│   ├── events/                   # event type registry, payload schemas, versioning
│   ├── storage/
│   │   ├── repositories.py       # the domain <-> event log bridge
│   │   ├── eventstore/           # port + adapters (in-memory now, Postgres at M3)
│   │   ├── projections/          # projector runtime + Phase 1 projections
│   │   └── semantic/             # port + pgvector adapter  [M3]
│   ├── retrieval/                # C2: the only cross-store reader  [M3]
│   ├── reasoning/
│   │   ├── router/               # tier 0 intent classification
│   │   ├── fast/                 # tier 1 projection-backed answers  [M3]
│   │   ├── deep/                 # tier 2 retrieval + synthesis  [M4]
│   │   ├── mutation/             # ProposedChange planner + validator
│   │   └── compose/              # grounded reply assembly + citations
│   ├── llm/                      # provider-agnostic port, mock, real adapter
│   ├── conversation/             # turn orchestration, session, trace
│   ├── privacy/                  # consent registry, export, erasure  [M5]
│   ├── interfaces/
│   │   ├── api/                  # HTTP surface  [M3]
│   │   └── cli/                  # swayam ask / sim / tasks / log
│   └── observability/            # structured traces, redaction
├── migrations/                   # Postgres DDL  [M3]
├── tests/
│   ├── unit/  contract/  integration/  arch/  evals/
├── evals/fixtures/               # recorded conversations + expected intents/citations
├── infra/                        # local compose, deployment
└── Makefile
```

Dependency rule, enforced in `tests/arch/test_dependencies.py`:

```
interfaces → conversation → reasoning → retrieval → storage
                                       ↘      ↗
                                        domain → events → core
```

`domain/` imports nothing from `storage/`, `reasoning/` or `llm/`, which is what makes
it testable with no database, no clock and no I/O. The repositories are the single
sanctioned bridge and therefore live in `storage/` — placing them in `domain/` would
have made the purity claim a fiction (ADR 0005). A reserved `health/` boundary sits
outside the graph entirely.

**Stack** (ADR 0006–0010): Python 3.12 + FastAPI, Postgres + pgvector, mock-only LLM
behind a structurally-typed port, local-first single-tenant, CLI now and HTTP at M3.

---

## 10. Module contracts (Phase 1 ports)

```python
class EventStore(Protocol):
    def append(self, stream: Stream, expected_version: int,
               events: list[Event], idempotency_key: str | None) -> AppendResult: ...
    def read(self, stream_id: UUID, from_seq: int = 0) -> Iterator[Event]: ...
    def read_all(self, since: Cursor) -> Iterator[Event]: ...

class SemanticStore(Protocol):
    def upsert(self, docs: list[MemoryDoc]) -> None: ...
    def search(self, query: str, filters: dict, k: int) -> list[MemoryHit]: ...

class IntentRouter(Protocol):
    async def route(self, utterance: str, *, today: date) -> IntentDecision: ...

class MutationPlanner(Protocol):
    async def plan(self, utterance: str, decision: IntentDecision, *,
                   today: date,
                   known_tasks: Sequence[Task] = ()) -> tuple[ProposedChange, ...]: ...

class LLM(Protocol):
    async def complete(self, *, purpose: str, instruction: str,
                       payload: dict[str, object],
                       schema: type[T]) -> T: ...   # structured output only
```

`LLM.complete` is **structurally typed**: the runtime is handed a schema and returns a validated
instance. Free-text generation is not exposed to the reasoning layer. This is what makes the
mock runtime a genuine substitute for the real provider rather than a degraded stand-in.

`IntentRouter` and `MutationPlanner` are **async** because the LLM is. A model-backed router
cannot honestly implement a synchronous protocol — the alternatives are `asyncio.run` inside a
running loop, or blocking the turn on a thread — and either one would make the transport of the
model a property of its callers. Making the protocols async costs two `await`s at the call
sites and buys implementations that do not care whether the reasoning behind them is a regex or
a hosted model. `DeterministicRouter` and `RuleBasedPlanner` implement the same protocols with
no I/O; `build_runtime(reasoning="llm")` swaps the pair and nothing downstream moves.

### 10.1 The no-silent-drop invariant

**Every instruction the user gave is either carried out or named in the reply.**

The failure this prevents is specific and was real: a planner that emitted at most one proposal
turned "mark the report done and also move the draft to Friday" into one change and a confident
reply that read as complete. The user had no way to learn that half their sentence was discarded.

It is enforced in three places, because any one of them can be violated alone:

1. **The router counts.** Every decision carries `instruction_count`, recorded *before* the
   LLM router grounds its instructions, so an invented instruction still registers.
2. **The planner proposes per instruction.** `LLMPlanner` binds each change to an
   `instruction_index` and drops anything out of range. An unresolvable reference is carried
   forward with no `target_id` rather than dropped, so the plan does not shrink to hide it.
3. **The orchestrator reconciles.** It compares `instruction_count` against the proposals it
   received and reports any shortfall, in the reply and as a `plan_gap` trace entry.

Reference resolution runs again immediately before each write. Instructions are applied in the
order given, so "add a task to send the invoice and also move the invoice to Friday" has to see
the task the first instruction created.

### 10.2 What the model is not allowed to do

The safety property is not "the model is careful". It is that several specific things are
structurally out of reach:

| Not allowed | Enforced by |
|---|---|
| Name a task by id | The `ProposedChangePayload` schema has no id field. The planner supplies the user's words; `resolve_task` turns them into an id. |
| Invent an instruction | Every instruction carries a `quote`, verified as a substring of the utterance. Ungrounded instructions are dropped and counted as a gap. |
| Set a field the command does not accept | `ALLOWED_FIELDS` per `ChangeKind`. Everything else is discarded before a command is built. |
| Act on a half-understood instruction | A change missing a required field produces no proposal, which surfaces as a clarification rather than a guess. |

A model-supplied `blocked_by` is the sharpest example. Blocking requires a reason; if the user
did not give one, the honest outcome is a question, not a plausible-sounding fabrication.

---

## 11. Conversational mock runtime

Purpose: validate the product thesis with no network, no API key, and deterministic output.

- **Mock LLM** — schema-valid responses from a fixture table, keyed by intent; deterministic
  by default, seedable for variance experiments.
- **Scenario runner** — `swayam sim <scenario>` replays multi-turn transcripts against the real
  domain, storage and reasoning code, with only the LLM faked.
- **Ship-first scenarios** (each is a Phase 1 acceptance test, written before the feature):
  - `capture_weekly_tasks` — natural language → correct events
  - `move_deadline` — mutation → correct event → confirmed reply
  - `am_i_overcommitted` — load arithmetic
  - `how_did_last_week_go` — cross-domain, cited
  - `stale_claim` — memory drift resolved correctly
  - `ambiguous_reference` — "fix it" → clarify, not guess
  - `llm_outage` — degradation to read-only (C9)
- **In-memory adapters** implement the same ports as Postgres, so every scenario runs in-process
  in under a second with no database.

---

## 12. Privacy baseline for Phase 1

Low-risk data, but the guarantees are established here or they never get established.

- Consent registry keyed by data category, default-deny, revocable. Categories in Phase 1:
  `conversations`, `tasks`, `temporal`. (`health` and `feedback` registered as permanently
  unavailable, so their Phase 2/4 introduction is an explicit act.)
- Transcript retention: default TTL, user-visible, configurable, purge tested.
- Structured logs carry correlation ids, never raw utterance text.
- Per-user data encryption key wrapped by a system key; key id stored per stream.
- Export and erasure exposed as first-class interface operations, not scripts.

---

## 13. Observability, testing, evals

**Observability.** One trace per turn: `turn_id → route decision (+confidence) → retrieved
units → proposed changes → committed events → final citations`. Non-determinism is a debugging
problem, so the trace is mandatory infrastructure, not observability nice-to-have. A
`why this answer` endpoint is a direct consequence of C10.

**Testing.**

| Suite | Scope | Gate |
|---|---|---|
| `unit` | `domain/`, `events/` — pure, no I/O, exhaustive transition table | required |
| `arch` | dependency rule, health boundary, no-LLM-in-domain | required |
| `contract` | one suite, run against in-memory *and* Postgres adapters | required |
| `integration` | full turn, real store, mock LLM | required |
| `projection` | replay full log from empty, assert convergence | required |
| `evals` | recorded transcripts, scored | required, non-regressing |

**Evals measure, not vibe-check:** intent accuracy, entity-resolution accuracy, mutation
validity (no invalid proposal ever commits), citation validity (C10), clarification rate (too high
= annoying, too low = overconfident), and end-to-end latency against the tier budgets.

---

## 14. Milestones

| # | Milestone | Exit criteria |
|---|---|---|
| M0 | Event core | `core/`, `events/`, `EventStore` + in-memory adapter, envelope + invariants green |
| M1 | Task domain | aggregates, transitions, `task_current` projection, replay test green |
| M2 | Conversational capture | mock runtime, `capture_weekly_tasks` + `move_deadline` scenarios pass |
| M3 | Temporal + load reasoning | agenda, `commitment_load_week`, `conversational_claim` projections |
| M4 | Cross-domain synthesis | `how_did_last_week_go` cited; staleness rule; clarification behaviour |
| M5 | Hardening | degradation, export/erasure, consent registry, eval harness, latency budget met |

Sequencing note: M2 before M3 deliberately. Capture must be trustworthy before querying it —
a retrieval system over unreliable writes produces confidently wrong answers, which is the
failure mode the framework most wants to avoid.

---

## 15. Non-goals for Phase 1

No health/biometric data or schema · no voice I/O · no feedback capture (schema seam only) ·
no wearable or calendar-provider OAuth · no proactive or unprompted insights (Phase 5) · no
multi-user or sharing model · no mobile client · no claims of cross-domain correlation beyond
temporal × task.

---

## 16. Resolved decisions

All five open decisions were answered and recorded as ADRs 0006–0010. The two that
carry the most weight downstream:

- **Vector strategy** (§6.3) is the deliberate under-build, and the decision most worth
  revisiting at the Phase 2 boundary.
- **Mock-only LLM** means Phase 1 validates plumbing, not product. The eval suite is the
  mitigation and must exist *before* a real provider is wired in, or the first live
  model will quietly absorb every unmeasured weakness.

Remaining unbuilt surface, and what gates it:

| Component | Milestone | Gate |
|---|---|---|
| `storage/semantic/` | M3 | pgvector schema + key; `SemanticStore` port |
| `retrieval/` | M3 | first cross-store join |
| `reasoning/fast/` | M3 | `agenda_day`, `commitment_load_week`, `conversational_claim` |
| `reasoning/deep/` | M4 | retrieval + cited synthesis |
| `privacy/` | M5 | consent, export, erasure |
| `interfaces/api/` | M3 | FastAPI adapter over `TurnOrchestrator.handle` |
| `migrations/` | M3 | event table DDL, append-only privileges |

Nothing in that table is stubbed with a fake implementation. Unbuilt paths return an
explicit "not yet" and write nothing, because a path that appears to work while reading
from projections that do not exist is worse than one that says it is not ready.
