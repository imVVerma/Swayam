# Architecture Decision Records

One file per irreversible decision. Format: Context → Decision → Consequences → Status.

Decisions are numbered sequentially, immutable once `Accepted`. Reversing an accepted
ADR requires a new ADR that supersedes it — the record of *why it changed* is the
"digital self" principle applied to the codebase.

| # | Title | Status | Blocks |
|---|---|---|---|
| 0001 | Event-sourced core with derived projections | Accepted (`docs/phase1_arch.md` §5) | — |
| 0002 | The LLM never writes to the event log; typed proposals only | Accepted (§7.3) | — |
| 0003 | Tiered reasoning: fast router vs. async deep path | Accepted (§7.1) | — |
| 0004 | Health store is a reserved boundary, not built in Phase 1 | Accepted (§6.4) | — |
| 0005 | Repositories live in `storage/`, keeping `domain/` I/O-free | Accepted | — |
| 0006 | Python 3.12 + FastAPI, pydantic for payloads | Accepted | M0 |
| 0007 | Postgres + pgvector, separate schema and key for the semantic layer | Accepted | M0 |
| 0008 | Mock-only LLM for M0–M2 behind a structurally-typed port | Accepted | M2 |
| 0009 | Local-first single-tenant, CLI now, no web client | Accepted | M2 |
| 0010 | In-memory event store for M0–M2; Postgres adapter at M3 | Accepted | M3 |
| — | Port/Protocol throughout; no DI container | Accepted | — |

---

## 0006 — Language and framework

**Context.** Phase 1 needs a web surface eventually, a strong eval story now, and
structured LLM output on the critical path of every mutation.

**Decision.** Python 3.12, FastAPI for the HTTP surface, pydantic v2 for payload and
aggregate models. The `LLM` port is structurally typed — callers pass a schema and get
a validated instance back, never free text.

**Consequences.** Evals and model tooling are first-class. End-to-end types are weaker
than TypeScript, which is a real cost; it is offset by making the domain pure and
running mypy in strict mode. Structured output as a type-level constraint is what makes
the mock a genuine substitute for a real provider.

## 0007 — Datastore and vectors

**Context.** Constraint C5 requires structured and semantic data to be separate storage
domains. Constraint C6 forbids a general-purpose data lake.

**Decision.** One Postgres cluster. The event log and projections are ordinary tables.
The semantic layer is a **separate schema with a separate encryption key**, behind a
`SemanticStore` port.

**Consequences.** This is the deliberate under-build: the framework's "layered storage"
is interpreted as separation of storage *domains and encryption boundaries*, not one
engine per layer. Cheaper to run, and a dedicated vector engine can be substituted
behind the port later without touching anything above `storage/`. **Revisit at the
Phase 2 boundary** — this is the decision most likely to need revisiting.

## 0008 — Mock-only model for M0–M2

**Context.** Shipping a live model during scaffolding makes tests non-deterministic
and costs money before the reasoning contract has proved itself.

**Decision.** Phase 1 runs against `MockLLM`. Routing is deterministic and rule-based.
No live provider until the reasoning contract is stable.

**Consequences.** Every scenario is reproducible and free. The risk is validating the
plumbing and mistaking it for validating the product — the eval suite in
`tests/evals/` is the mitigation, and it must be built *before* the real provider is
wired in, not after.

## 0010 — In-memory storage for M0–M2

**Context.** Durability is not what Phase 1 is testing; the event-sourcing discipline
is.

**Decision.** `InMemoryEventStore` until M3. The `EventStore` contract suite in
`tests/contract/` runs against whatever adapters exist, so adding Postgres is one
fixture and the rest of the codebase does not move.

**Consequences.** State does not survive between CLI invocations, which the CLI states
plainly rather than hiding. The trade is real but temporary: the contract suite is what
makes the swap safe, and it is written now so it cannot be skipped later.

## 0011 — Async reasoning protocols

**Context.** `IntentRouter.route` and `MutationPlanner.plan` were synchronous. The
LLM port that a model-backed implementation must call is `async`. A model-backed
router therefore cannot implement the sync protocol honestly: the alternatives are
`asyncio.run` from inside a running loop, or blocking a turn on a worker thread.

**Decision.** Make both protocols `async`. The deterministic implementations become
trivially async and keep their behaviour. The orchestrator gains two `await`s and
nothing else changes.

**Consequences.** Both planner and router implementations are now interchangeable
at runtime (`build_runtime(reasoning="llm")`), and adding a provider is a composition-root
change rather than a rewrite. The cost is that every caller must be async, which the
orchestrator already was.

## 0012 — LLM-backed router and planner behind the same protocols

**Context.** ADR 0008 chose a mock-only model for M0–M2, with the reasoning contract
proved against deterministic implementations first. That leaves the question of
whether the LLM implementations are real or degraded.

**Decision.** Build `LLMRouter` and `LLMPlanner` against the `LLM` port now, and hold
both to the same protocols as the deterministic pair. Run them against `MockLLM` with
fixed fixtures, so the tests exercise the *laundering* — grounding, index bounds,
field allowlists, reference resolution — rather than the model.

**Consequences.** `tests/unit/test_llm_reasoning.py` and
`tests/integration/test_llm_pipeline.py` assert that the LLM path is a substitute and not
a stand-in, and they fail if any of the checks are removed. No live provider is wired;
that still needs a provider and credential decision, and ADR 0008's eval suite in
`tests/evals/` is still the gate before one is chosen.

## 0013 — No silent partial execution

**Context.** A turn containing several instructions produced at most one change and a
reply that read as complete. The user could not tell that the rest of their sentence had
been discarded — the worst available failure, because they would act on a belief the
system never checked.

**Decision.** Every instruction the user gave is either carried out or named in the
reply. Enforced in three places: the router reports `instruction_count`; each planner
emits at most one proposal per instruction and carries unresolvable references forward
unresolved rather than dropping them; the orchestrator reconciles the two counts, reports
any shortfall in the reply, and records a `plan_gap` trace entry.

**Consequences.** Partial turns now produce a reply that is both a confirmation and a
question, which is a deliberate departure from the previous either/or. The trade is that a
single unhandleable instruction can cost the user a round trip they did not need to make;
the alternative was silently rewriting the wrong commitment, which is worse. Reference
resolution is re-run at write time so a later instruction in the same turn can act on an
earlier one.
