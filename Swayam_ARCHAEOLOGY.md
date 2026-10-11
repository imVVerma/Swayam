# Swayam — Project Archaeology

**Reconstructed:** 2026-10-07 · based on repository inspection only (no files modified)

**Confidence markers used throughout:**
- **[confirmed]** — directly visible in code, docs, tests or git history
- **[inferred]** — strongly implied by evidence but not stated outright
- **[uncertain]** — plausible but not provable from what remains

---

## 1. What is this project?

**Swayam** is a personal intelligence platform built as a *conversational interface over
your own commitments* — you type natural language ("add a task to send the Q3 report
due friday", "am I overcommitted this month") and the system routes, plans, validates
and records it as events. **[confirmed]**

It is explicitly **Phase 1 of a 5-phase plan**: only temporal awareness + task tracking,
conversational text only. Health data, voice, feedback capture, and proactive insights
are deliberately deferred to Phases 2–5. The repository header says this in its first
three lines (`README.md:1–8`), and a 651-line technical design document
(`docs/phase1_arch.md`) specifies the whole architecture down to SQL schemas and
invariant numbers.

The project is a **modular monolith in Python 3.12** with an event-sourced core: an
append-only event log is the single source of truth, and every readable view
(projections) is derived by replay. **[confirmed]**

## 2. What problem was it intended to solve?

The stated problem is **commitment reconciliation** — the "minimum viable cross-domain
insight" question extracted from a strategic framework document that lives in the repo
root (`Personal Intelligence Platform_ Strategic Framework.html`): **[confirmed]**

> *"What did I say I'd do this week?"* vs. *"what actually happened"* vs. *"what's
> ahead"* — using only temporal and task data.

The larger vision (Phase 2+): one chat-like interface over *all* personal data domains
(tasks, calendar, feedback, health), whose differentiator is **contextual synthesis
across domains** rather than five apps with a chatbot bolted on (`README.md:10–12`).
Phase 1 exists to validate that thesis with the lowest-risk data available, before any
sensitive (health) data exists.

Secondary problems the design explicitly attacks: **[confirmed]**
- LLMs hallucinating / guessing on ambiguous references (the system must *ask*)
- silently dropping part of a multi-instruction sentence (ADR 0013)
- data lakes that dissolve privacy boundaries later (health store tripwire test)
- unreplayable state (event sourcing; no `updated_at`, tombstoned erasure)

## 3. What evidence in the code supports that interpretation?

| Evidence | Where | What it shows |
|---|---|---|
| Strategic framework HTML with §1–§10 | repo root | the original vision doc; `phase1_arch.md` cites its sections (§3, §4.2, §6.2, §8…) as constraints |
| 651-line Phase 1 design doc | `docs/phase1_arch.md` | full spec: constraints table C1–C12, event catalogue, tiered reasoning, milestones M0–M5 |
| 13 ADRs | `docs/adr/README.md` | irreversible decisions with context/consequences; ADR 0013 tells the story of a real silent-partial-execution bug |
| Milestone-labelled stubs | `reasoning/fast/README.md`, `retrieval/README.md`, `storage/semantic/README.md`, `privacy/README.md` | unbuilt components documented with the milestone that gates them; explicitly *no fake implementations* |
| Scenarios marked `pending_milestone="M3"/"M4"` | `interfaces/cli/scenarios.py:147–167` | query scenarios exist and are asserted to *decline* until projections land |
| `NOT_YET` reply constant | `conversation/turn.py:60` | "That path arrives in M3…" — the system is honest about what it cannot do |
| Event catalogue with health/feedback absent | `events/catalogue.py:1–13` | "Health and feedback events are deliberately absent" — reserved boundary |
| Architecture fitness tests | `tests/arch/test_dependencies.py` | enforces the layering *and* a tripwire that no Phase 1 module can import a health module |
| Comments describing past bugs | e.g. `router/deterministic.py:9–18`, `resolve.py:43–49` | several comments narrate specific regressions ("…which is how half a user's sentence got discarded") |
| Single git commit | `git log` | one commit: *"Harden Phase 1 reasoning: async LLM path, derived vocab, correct resolution"* (2026-09-30), message references "447 passed" |
| CLI demo clock pinned to 2026-09-29 | `interfaces/cli/app.py:35` | reproducible demos; matches README's example transcript date |

## 4. What are the main technologies used?

**Confirmed in use (imported in `src/`):** **[confirmed]**

| Tech | Role |
|---|---|
| Python 3.12 | language (pattern matching, `StrEnum`, `NewType`, dataclass `slots`) |
| Pydantic v2 | event payload schemas, aggregate validation, LLM structured output |
| Typer | CLI (`swayam ask/sim/tasks/log`) |
| pytest + pytest-asyncio | 450-test suite incl. architecture fitness tests |
| ruff, mypy (strict) | lint + type gate; `make lint typecheck` currently clean on `src/` |
| hatchling | build backend |

**Declared in `pyproject.toml` but never imported anywhere in `src/`:** **[confirmed]**
`fastapi`, `uvicorn`, `asyncpg`, `structlog` — these are *scaffolding for the planned
M3 HTTP/Postgres surface*, not yet wired. The `interfaces/api/` package is an empty
`__init__.py`.

**Deliberately absent:** no database, no Docker, no CI config, no `.env`/env example,
no network calls of any kind.

## 5. What is the architecture?

A **layered event-sourced modular monolith**, enforced by tests rather than convention
(`docs/phase1_arch.md` §9):

```
utterance → conversation/turn → reasoning/router (tier 0, deterministic)
                                   ├─ MUTATION → reasoning/mutation planner
                                   │                → ProposedChange (pure data)
                                   │                → domain/ invariants (pure)
                                   │                → storage/eventstore (append-only)
                                   │                → storage/projections (fold)
                                   └─ QUERIES → [M3/M4, currently "not yet"]
                                → reasoning/compose (reply + citations)
```

Dependency rule (checked by `tests/arch/test_dependencies.py` by parsing the import
graph): **[confirmed]**

```
interfaces → conversation → reasoning → retrieval → storage
                                       ↘      ↗
                                        domain → events → core
```

Four load-bearing properties: **[confirmed]**
1. The event log is the only source of truth; projections are disposable views.
2. The model never writes to the log — it emits `ProposedChange` data structures that
   the domain validates like any other command.
3. `domain/` is pure (no I/O, no clock, no database); repositories live in `storage/`
   as the single sanctioned bridge.
4. Uncertainty is surfaced as a question, never resolved by guessing.

Tiered reasoning (§7): tier 0 router <150 ms → tier 1 fast path <300 ms → tier 2 deep
path async 2–10 s. Only tier 0 + the mutation path exist today.

## 6. What are the major components?

| Component | Path | Status |
|---|---|---|
| Core primitives | `src/swayam/core/` | **done** — ids (NewType), `Clock`/`FrozenClock`, error hierarchy; imports nothing |
| Event envelope + registry + catalogue | `src/swayam/events/` | **done** — 19 Phase 1 event types with pydantic payloads; frozen-dataclass envelope with `occurred_at`/`recorded_at` dual clocks, `causation_id`, idempotency key |
| Domain aggregates | `src/swayam/domain/` | **done** — `Task` (commands, `ALLOWED_TRANSITIONS`, reducer) and thin `Project`; invariants I1–I7 |
| Event store | `src/swayam/storage/eventstore/` | **port + in-memory adapter done**; Postgres adapter planned for M3 |
| Repositories | `src/swayam/storage/repositories.py` | **done** — `TaskRepository`, `ProjectRepository.ensure_default` |
| Projection | `src/swayam/storage/projections/task_current.py` | **done** — replay-convergent; 4 more projections (agenda_day, commitment_load_week, task_timeline, conversational_claim) specified but unwritten |
| Deterministic router (tier 0) | `src/swayam/reasoning/router/deterministic.py` | **done** — rule-based, clause-splitting, date extraction, action heads |
| LLM router | `src/swayam/reasoning/router/llm_router.py` | **done against MockLLM** — quote-grounding, confidence bounds, fallback to deterministic |
| Rule-based planner | `src/swayam/reasoning/mutation/rule_planner.py` | **done** — one proposal per instruction |
| LLM planner | `src/swayam/reasoning/mutation/llm_planner.py` | **done against MockLLM** — field allowlists, index bounds |
| Proposal/validation layer | `.../mutation/proposal.py`, `resolve.py` | **done** — `ChangeKind`, `REQUIRED_FIELDS`, unified `match_candidates` resolver |
| Reply composer with citations | `src/swayam/reasoning/compose/reply.py` | **minimal but done** — `Citation`, `Reply.clarification` |
| Turn orchestrator | `src/swayam/conversation/turn.py` | **done for mutations** — route→plan→validate→append→project→compose, plan-gap reporting |
| LLM port + mock | `src/swayam/llm/` | **done** — structurally-typed `LLM` protocol; `MockLLM` fixture table with seedable variance |
| CLI | `src/swayam/interfaces/cli/` | **done** — `ask`, `sim` (13 scenarios), `tasks`, `log` |
| Tracing | `src/swayam/observability/trace.py` | **done** — per-turn trace, no raw utterance text in logs |
| Runtime composition root | `src/swayam/runtime.py` | **done** — `build_runtime(reasoning="deterministic"|"llm")` |
| HTTP API | `src/swayam/interfaces/api/` | **empty** (M3) |
| Retrieval / semantic store / fast+deep reasoning / privacy | respective README stubs | **documented, unwritten** (M3–M5) |
| Postgres migrations | `migrations/README.md` | **empty** (M3) |
| Infra | `infra/` | **empty** |

## 7. What is the data flow?

**Write path (works end to end):** **[confirmed]**

```
text utterance
  → DeterministicRouter.route()          # intent + slots + instruction_count
  → RuleBasedPlanner.plan()              # ProposedChange[] (pure data, no side effects)
  → TurnOrchestrator._with_live_target() # re-resolve refs against fresh projection
  → Task.decide(command, ctx)            # pure invariant check → Event[]
  → InMemoryEventStore.append()          # registry validation, seq allocation,
                                         # optimistic concurrency, idempotency dedupe
  → TaskCurrentProjection.apply()        # incremental fold (idempotent per seq)
  → Reply(text, citations, clarification) + Trace entries
```

**Read path:** `swayam tasks` reads `TaskCurrentProjection.open_tasks()`;
`swayam log` reads `EventStore.read_all()` directly.

**Query path (queries)** — `STATE_QUERY`, `TEMPORAL_QUERY`, `LOAD_QUERY`,
`CROSS_DOMAIN` — routes correctly but returns `NOT_YET` and writes nothing
(`conversation/turn.py:179–188`). Their projections land in M3/M4. **[confirmed]**

## 8. What can the current implementation actually do?

Verified by running it: **[confirmed]**

- ✅ **450/450 core tests pass** (`tests/unit tests/contract tests/arch tests/integration`,
  run in ~1 s) — unit (domain/events/router/resolve/projections), contract (event store),
  architecture (layering + health tripwire), integration (full turns, LLM pipeline).
- ✅ `mypy --strict` clean on all 47 source files; `ruff check src` clean.
- ✅ Natural-language task capture with due dates: *"add a task to review the budget
  due friday"* → `Added "review the budget", due 2026-10-02. [mutation · 2 events]`
- ✅ Full task lifecycle by conversation: create, rename, note, block/unblock,
  complete/reopen, reschedule, archive — each confirmed with a citation.
- ✅ Multi-instruction turns: two requests → two changes → two confirmations; any
  shortfall is *reported*, never hidden (ADR 0013 no-silent-partial-execution).
- ✅ Ambiguity → clarification, never a guess: *"mark the report done"* with two
  matching tasks → candidate list question, 0 events.
- ✅ Low-confidence routing declines (*"fix it"* → "I didn't follow that").
- ✅ Query intents are correctly classified but explicitly declined
  (*"am I overcommitted this month"* → `That path arrives in M3… [load_query · 0 events]`).
- ✅ 13 replayable scenarios via `swayam sim <name>`, incl. 3 pending-milestone ones
  asserted to decline.
- ✅ LLM path (`build_runtime(reasoning="llm")`) runs against `MockLLM` fixtures with
  quote-grounding checks — no live provider, no API keys.
- ⚠️ **State does not persist** between CLI invocations (in-memory store, stated
  plainly by the CLI rather than hidden).

## 9. What appears unfinished?

By the project's own milestone table (`phase1_arch.md` §14): **[confirmed]**

- **M3:** temporal + load query projections (`agenda_day`, `commitment_load_week`,
  `conversational_claim`), semantic store (pgvector), `retrieval/`, `reasoning/fast/`,
  Postgres adapter + `migrations/` DDL, HTTP API.
- **M4:** `reasoning/deep/` — cross-domain synthesis with citations
  (*"how did last week go"*), staleness reconciliation.
- **M5:** hardening — export/erasure, consent registry, degradation tests, latency
  budgets.
- Phase 2/4/5 product surface (feedback capture, voice, health, proactive insights) —
  deliberately deferred, not accidentally missing.
- `interfaces/api/__init__.py` and `src/swayam/evals/` (empty package) exist as
  placeholders.
- Eval suite: only 5 fixture cases exist against a spec that anticipates many more;
  `evals/baseline/` is an empty directory.
- Declared-but-unused dependencies (`fastapi`, `uvicorn`, `asyncpg`, `structlog`) are
  the M3 stack bought in advance.

## 10. What appears broken?

- ❌ **2 of 5 eval-gate tests fail** (`tests/evals/test_eval_gate.py`):
  - `malformed_date`: expects clarification about `required_field` (due_date), but the
    orchestrator answers about `target` — the fixture seeds a task named "budget
    report" yet the resolution/`_why_no_proposal` path classifies the question as
    target-related.
  - `overconfidence_trap`: fixture routes with `confidence: 0.3` expecting a
    `low_confidence` clarification, but observed as `target` — the low-confidence path
    in `TurnOrchestrator.handle` (`decision.is_confident` → "I didn't follow that") is
    apparently not reached as the classifier assumes (the mock route fixture's
    confidence handling differs from the expectation).
  - Net effect: outcome and commits still match; only the *clarification taxonomy*
    diverges. Either the classifier (`tests/evals/classify.py`) or the fixtures is
    stale relative to current orchestrator wording.
- ❌ **12 ruff errors in `tests/evals/`** (unsorted imports, line >100, ambiguous
  en-dashes/non-breaking hyphens in comments, unused `asyncio` import, unused
  `seed_tasks` variable, redundant `noqa`). `make lint` (`ruff check src tests`) fails;
  only `src/` is clean.
- ⚠️ **`make test` fails on this machine** — the Makefile calls bare `python`, which is
  not on PATH (only `python3` / `.venv/bin/python`). Works via
  `.venv/bin/python -m pytest …`.
- ⚠️ **Fixture triplication with drift**: the same eval YAML fixtures exist in
  `evals/fixtures/`, `tests/evals/fixtures/` and `evaluations/fixtures/`, and
  `malformed_date.yaml` differs between them (`confidence: 0.3` vs `0.8`). Only
  `tests/evals/` is wired into pytest. This looks like an aborted reorganisation.
- ⚠️ **Demo clock frozen at 2026-09-29** — relative dates resolve against that date
  (`friday` → 2026-10-02), so transcripts are reproducible but the dates are now in
  the past relative to real time. Deliberate for demos; stale if mistaken for real use.
- Nothing in the core pipeline fails: all 450 non-eval tests pass.

## 11. What appears to be experimental / prototype code?

- **`reasoning/router/llm_router.py` + `reasoning/mutation/llm_planner.py`** — real
  LLM-backed implementations proven only against `MockLLM` fixtures (ADR 0012). No
  live provider is wired; these are the seam where a real model would plug in.
- **`llm/mock.py`** — fixture table + optional seedable RNG for variance experiments;
  explicitly a stand-in until a provider decision is made.
- **`tests/evals/`** — an eval *harness* (spec, classify, fixtures, gate) built ahead
  of the model it is meant to judge; ADR 0008 insists it must exist before a real
  provider is chosen. Currently half-finished (5 cases, 2 failing).
- **`prepare-for-claude.sh`** — a handoff script that zips the project (with a tree
  map and git log) to a Windows desktop via `/mnt/c` — evidence this repo was
  regularly shipped to an external AI assistant for collaboration. Not part of the
  product.
- **`FrozenClock(DEMO_NOW)` everywhere in the CLI** — every command builds a fresh
  in-memory runtime with a pinned clock; a demo scaffold, not a session design.
- **Empty placeholder packages** (`interfaces/api/`, `src/swayam/evals/`, `infra/`,
  `evals/baseline/`) — reserved names, no fake code (which is itself a stated policy:
  `phase1_arch.md:649–651`).

## 12. What external APIs/services does it depend on?

**None.** **[confirmed]** The README states it outright (`README.md:71`): "No database,
API key or network access is required for anything in this repo yet." All reasoning
runs through `MockLLM`; storage is in-process. The `LLM` protocol (`llm/port.py`) is
the designed seam for a future provider (OpenAI/Anthropic/etc. — **no provider is
named anywhere in the repo** [confirmed]); `asyncpg`/Postgres is the planned M3
store (ADR 0010). pgvector is specified for M3 (ADR 0007).

## 13. What configuration or environment variables does it require?

**None required.** **[confirmed]**
- No `.env`, no `.env.example` (only `.gitignore` anticipates them).
- Configuration surface: `pyproject.toml` (deps, pytest, ruff, mypy strict) and
  `Makefile`.
- Runtime knobs exist as function parameters, not env vars:
  `build_runtime(clock=…, user_id=…, reasoning="deterministic"|"llm", llm=…)`.

## 14. How would I run it locally?

```bash
python3 -m venv .venv && source .venv/bin/activate
make dev                              # pip install -e ".[dev]"
# if ensurepip is missing (Debian/Ubuntu):
#   python3 -m venv --without-pip .venv && curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python

make test                             # 450 tests (NOTE: needs `python` on PATH or use .venv/bin/python -m pytest tests/unit tests/contract tests/arch tests/integration)
.venv/bin/python -m ruff check src    # clean (tests/evals has 12 known errors)
.venv/bin/python -m mypy              # strict, clean

swayam sim                           # list scenarios
swayam sim capture_weekly_tasks      # replay one
swayam ask "add a task to send the Q3 report due friday"
swayam tasks                         # projection view (fresh runtime each time)
swayam log                           # event log (fresh runtime each time)
```

(Full paths above assume `.venv` activation; `make test`/`make lint` as written
require `python`/`ruff` on PATH — see §10.)

## 15. What would I see if it runs successfully?

Verified live: **[confirmed]**

```
$ swayam sim capture_weekly_tasks
Swayam
  Tuesday 29 September 2026 · in-memory event log

capture_weekly_tasks — Natural language becomes correct events, and only correct events.

> add a task to send the Q3 report
  Added "send the q3 report".
  [mutation · 1 events]
> add a task to review the budget due friday
  Added "review the budget", due 2026-10-02.
  [mutation · 2 events]
> add a task to email the client
  Added "email the client".
  [mutation · 1 events]

  3 open task(s) in the projection
    · review the budget  due 2026-10-02
    · email the client  undated
    · send the q3 report  undated
```

Queries decline honestly:

```
$ swayam ask "am I overcommitted this month"
> am I overcommitted this month
That path arrives in M3. Right now I can capture and update tasks.
  load_query · 0 events written
```

And `swayam tasks` in a *new invocation* shows nothing plus the plain-language
explanation that storage is in-memory in Phase 1.

## 16. What are the most important files and what does each do?

| File | Role |
|---|---|
| `README.md` | project identity, status, layout, roadmap — the front door |
| `docs/phase1_arch.md` | the TDD: constraints C1–C12, event schema, reasoning tiers, invariants I1–I7, milestones M0–M5. **The single densest artifact** |
| `docs/adr/README.md` | 13 ADRs — why each irreversible decision was made (0008 mock-only LLM, 0010 in-memory store, 0013 no silent partial execution) |
| `Personal Intelligence Platform_ Strategic Framework.html` | the original product vision the whole build is derived from (§1–§10) |
| `src/swayam/domain/task.py` | the Task aggregate: commands in, events out, reducer; all invariants enforced here |
| `src/swayam/events/catalogue.py` | the event log design in executable form (19 typed event payloads) |
| `src/swayam/storage/eventstore/port.py` + `memory.py` | the `EventStore` contract and its reference adapter (seq, concurrency, idempotency) |
| `src/swayam/storage/repositories.py` | the only legal bridge between domain and store |
| `src/swayam/conversation/turn.py` | the orchestrator — the pipeline wiring, plan-gap reconciliation, clarification selection |
| `src/swayam/reasoning/router/deterministic.py` | tier-0 intent classification + clause splitting (largest single source file, 406 lines, densest bug-history comments) |
| `src/swayam/reasoning/mutation/rule_planner.py` + `proposal.py` + `resolve.py` | the mutation safety pipeline: typed proposals, required-field table, unified reference resolution |
| `src/swayam/reasoning/router/llm_router.py` + `mutation/llm_planner.py` | the LLM path behind the same protocols, quote-grounding and allowlists |
| `src/swayam/runtime.py` | the composition root — the wiring *is* the architectural claim |
| `src/swayam/interfaces/cli/app.py` + `scenarios.py` | the product surface (4 verbs) and 13 acceptance scenarios |
| `tests/arch/test_dependencies.py` | the fitness functions that make the layering and health boundary checkable claims |
| `tests/evals/` | the pre-model eval harness (currently 3 pass / 2 fail) |
| `Makefile`, `pyproject.toml` | dev workflow + strict tool config |

## 17. Is there evidence of the original motivation or design decisions?

Strong, layered evidence: **[confirmed]**

1. **Motivation:** the Strategic Framework HTML (vision: "digital self", cross-domain
   reasoning, privacy-by-architecture) → `phase1_arch.md` extracts it into 12 testable
   constraints → the code enforces them. The chain is explicit and section-referenced.
2. **ADRs** record each decision with Context/Decision/Consequences/Status — including
   the *social* rule that accepted ADRs are immutable and reversals need a superseding
   ADR ("the 'digital self' principle applied to the codebase", `docs/adr/README.md:6`).
3. **Bug archaeology in comments:** many comments narrate real failures and their fixes —
   four drifted verb lists ("three different word sets… they disagreed twice"),
   the `resolve_task`/`describe_candidates` duplication ("the user was shown a
   candidate list missing the task whose title was literally the phrase they had
   typed"), the off-by-one in `read_all`, `COMPLETE → COMPLETE` being absent as a
   deliberate no-op. This is a codebase that learned in public.
4. **ADR 0013** documents the motivating incident for no-silent-partial-execution: a
   multi-instruction turn produced one change and a confident reply.
5. **The single git commit** message summarises the last hardening round and cites
   "447 passed" — the test count grew to 450 after the eval suite was added (evals are
   excluded from `make test`).

## 18. What technical concepts was I probably trying to learn?

Ranked by how much of the codebase is devoted to each: **[inferred]**

1. **Event sourcing & CQRS** — append-only log, dual clocks, per-stream seq, optimistic
   concurrency, idempotency keys, rebuildable projections, replay-convergence tests.
2. **LLM application safety / structured output** — "the model never writes": typed
   `ProposedChange` plans, schema-validated `LLM.complete`, quote-grounding
   (instructions must be verbatim substrings), field allowlists, confidence bounds,
   fallback degradation.
3. **Eval-driven development before model integration** — building the eval harness
   first so a future provider can't absorb unmeasured weaknesses (ADR 0008).
4. **Hexagonal architecture / ports & adapters** — `Protocol` interfaces everywhere, no
   DI container, composition root as the wiring document.
5. **Architecture fitness functions** — import-graph parsing tests, reserved-module
   tripwires (health boundary).
6. **DDD aggregates & invariants** — state machines with explicit legal transitions,
   commands vs. events, pure domain.
7. **Error UX for conversational systems** — clarify-not-guess, candidate lists,
   no-silent-partial-execution, observational phrasing for load (C11).
8. **Privacy-by-architecture** — reserved boundaries, tombstoned erasure designed
   before sensitive data exists.

The project reads as a **senior-level learning/portfolio exercise in building an
LLM-mediated system "the right way"** — architecture and safety discipline first,
product second. [inferred]

## 19. What would be required to turn this into a clean, presentable project?

**Quick wins (hours):** **[confirmed]**
1. Fix or re-specify the 2 failing eval fixtures (`malformed_date`,
   `overconfidence_trap`) — decide whether classify.py or the fixture is authoritative.
2. `make lint` green: apply ruff autofixes to `tests/evals/` (~5 auto-fixable) and
   hand-fix the rest (line length, en-dashes, unused vars).
3. Deduplicate fixture dirs: keep `tests/evals/fixtures/` as the single source; delete
   `evals/fixtures/` (or fold its README into docs) and `evaluations/`.
4. Fix the Makefile to use `python3`/`.venv` (or document activation as required).
5. Remove empty `src/swayam/evals/` and `evals/baseline/`, or give them purpose.
6. Trim unused deps from `pyproject.toml` (`fastapi`, `uvicorn`, `asyncpg`,
   `structlog`) until M3, *or* mark them explicitly as M3-planned in a comment.

**For a portfolio README (medium):** add a short "what works today vs. what's
specified" section (the current README is excellent but long), a diagram generated
from the arch test's own ALLOWED table, CI (GitHub Actions running
`make test lint typecheck`), and either delete or explain `prepare-for-claude.sh`.
Suggested README structure in Appendix B.

**For real usefulness (large — the M3 milestone):** Postgres adapter + migrations,
the four M3 projections, and the query paths — that is what turns "capture tool that
forgets" into the product the framework describes.

## 20. What would you recommend I do with it?

**Recommendation: FINISH (to the Phase 1/M3 line) — and present it — rather than
rebuild, archive, or abandon.** Reasoning:

- **Don't abandon or archive as-is:** the hard parts (event-sourcing discipline,
  the LLM safety pipeline, architecture tests, eval harness design) are done and
  tested. Throwing that away would discard exactly the learning.
- **Don't rebuild:** the layering is sound and *checked by tests*; a rewrite would
  reproduce the same design with fewer proofs.
- **Extracting an idea** is the fallback: the two most portable ideas are (a) the
  "model never writes — typed proposals validated by a pure domain" pipeline and
  (b) the quote-grounded instruction router. Both are small enough to lift into
  another project if Swayam itself loses priority.
- **Finishing to M3 is bounded and high-leverage:** four projections + a Postgres
  adapter + query answers would make the flagship scenarios
  (`am_i_overcommitted`, `stale_claim`) actually answer instead of declining —
  that is the moment the demo becomes compelling. Everything is spec'd in
  `phase1_arch.md` §6–§8 with acceptance scenarios already written as pending.
- Minimum viable cleanup first (§19 quick wins), because right now `make lint` and
  two eval tests are red — a project should never present with its own gates failing.

**Sequencing suggestion:** quick wins → M3 projections (queries answerable on the
in-memory store) → Postgres only if persistence is actually needed for a demo.

---

# Appendices

## A. Five-sentence explanation you could give another person

1. **Swayam** is a personal "digital self" assistant — you type natural language about
   your tasks and deadlines, and it turns them into correct, auditable records instead
   of guessing.
2. It is built event-sourced: an append-only event log is the single source of truth,
   everything you read is a replayable projection, and the whole layering is enforced
   by architecture tests.
3. Its signature safety property is that the LLM never writes anything — it proposes
   typed changes that a pure domain model validates, and when it is unsure (ambiguous
   task references, missing deadlines) it asks you a question rather than picking.
4. Phase 1 (what's in the repo) covers task capture and updates end-to-end behind a
   CLI, with 450 passing tests and a pre-built eval harness; the cross-domain query
   answers ("am I overcommitted?") are specified and deliberately decline until the
   M3 projections land.
5. The repo also contains the original strategic framework document, a 651-line
   technical design, and 13 ADRs, so the *why* behind every decision is preserved
   alongside the code.

## B. Suggested GitHub README structure

```
# Swayam
  one-liner + badges (tests, lint, typecheck, py version)
  15-second demo GIF/terminal block (swayam sim capture_weekly_tasks)

## What works today            ← honest status table: works / declines-until-M3 / specified-not-built
## Try it in 60 seconds        ← venv + make dev + one command
## How it works                ← the 6-line pipeline diagram + four safety properties
## Architecture                ← layered diagram + "enforced by tests/arch/" note
## Project layout              ← the existing path table
## Roadmap                     ← phases 1–5 table (already good)
## Design docs                 ← links to phase1_arch.md, ADRs, strategic framework
## Development                 ← make targets, test/lint/typecheck, eval lane
## Non-goals & privacy posture ← from §12/§15 of the design doc
## License
```

## C. The 5 most important things to personally understand before claiming you built this

1. **Why the model must never touch the event log** — the `ProposedChange` → domain
   validation → append pipeline, and what failure it prevents (a malformed or
   hallucinated plan becoming a corrupted aggregate). Be able to explain ADR 0002 and
   where each check lives (`proposal.py` allowlists, `resolve.py`, `turn.py:_apply`).
2. **What event sourcing buys and costs here** — why `seq`, `occurred_at` vs
   `recorded_at`, `expected_version`, and idempotency keys exist; how
   `task_current` is rebuilt by replay and what "projection convergence" means
   (`tests/unit/test_projection_replay.py`).
3. **The two-tier routing design and its intent taxonomy** — why tier 0 is
   deterministic and <150 ms, what each of the 8 intents routes to today, and
   exactly which ones decline with `NOT_YET` and why guessing would be worse.
4. **The layering claim and how it is enforced** — the dependency rule, why
   repositories live in `storage/` not `domain/`, and how
   `tests/arch/test_dependencies.py` parses imports to check it (including the health
   tripwire).
5. **What "done" means for each milestone** — you must be able to say precisely what
   M0–M2 deliver, what M3–M5 don't yet, and why the eval suite had to exist before
   any live model (ADR 0008) — including the fact that 2 of 5 eval cases currently
   fail.

## D. Unanswered questions

1. **Why only one commit?** The entire project landed as a single commit on 2026-09-30.
   Was it squashed from a longer session, developed elsewhere and imported, or
   generated/assisted in one sitting? `prepare-for-claude.sh` suggests heavy AI-assisted
   workflow, but the git history can't confirm the actual authorship process.
2. **Which eval is wrong?** For `malformed_date` / `overconfidence_trap` (§10) — did
   the orchestrator's clarification wording change after the fixtures were written, or
   were the fixtures aspirational? No note in the repo explains it.
3. **What drove the fixture triplication** (`evals/`, `tests/evals/`,
   `evaluations/`) with differing confidence values? Which copy was intended to win?
4. **Was a live LLM provider ever tried?** ADR 0012 builds the LLM path against
   `MockLLM` only; nothing records which provider was contemplated or if one was ever
   called. (No API keys, no adapter, no prompt-tuning artifacts exist.)
5. **What is the `Personal Intelligence Platform_ Strategic Framework.html`'s
   provenance?** It is called the "source of truth" — hand-written, AI-generated, or
   produced by an earlier planning tool? No metadata explains it.
6. **Is the frozen demo date (2026-09-29) meaningful** (e.g. the day Phase 1 started)
   or arbitrary?
7. **Was Postgres/`asyncpg`/`structlog` ever exercised**, or only declared? Nothing
   in the repo records a spike or discarded spike branch.
8. **Was there ever a web/UI sketch?** The framework has a "Sample Interface Sketch"
   section, and ADR 0009 says "CLI now, no web client" — but no evidence shows whether
   a UI was prototyped and discarded.
9. **What happened between "447 passed" (commit message) and 450 today** — the eval
   suite added 5 cases (3 pass/2 fail); was the eval lane ever green?
10. **Who is the intended user** — only the author (local-first single-tenant per
    ADR 0009), or was a wider audience ever considered? The framework's
    investor/developer-questions section suggests a pitch, but the build is personal.
