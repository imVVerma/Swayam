# Swayam

A personal intelligence platform: one conversational interface over your own
commitments — temporal awareness, tasks, and (later) everything else.

**Phase 1 — Foundation.** Temporal + task tracking under a single conversational text
interface. No health data, no voice. Both are deferred deliberately, not merely
unbuilt; see [`docs/phase1_arch.md`](docs/phase1_arch.md) for why.

> The differentiator is not any single module. It is contextual synthesis — correlating
> data *across* domains rather than presenting five apps with a chatbot bolted on.

---

## Status

Milestones M0 and M1 are built and green: the event-sourced core, the Task aggregate
with its invariant table, the projection layer, and a deterministic router plus
rule-based mutation planner driving a real conversational turn.

The query paths — *"what's on today"*, *"am I overcommitted"*, *"how did last week
go"* — **do not work yet.** Their projections land in M3. They return an explicit
"not yet" and write nothing, which is the correct behaviour for a system whose whole
premise is that it should not guess.

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
```

And when it is not sure, it asks rather than guessing:

```
$ swayam sim ambiguous_reference
> mark the report done
  I wasn't sure what to change.
  Which task, and what should happen to it?
  [mutation · 0 events]
```

---

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
make dev          # runtime + dev dependencies
make test         # 272 tests: unit, contract, integration, architecture
make lint typecheck
```

If your Python has no `ensurepip` (common on Debian/Ubuntu), bootstrap pip inside the
venv:

```bash
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
```

No database, API key or network access is required for anything in this repo yet.

## Using it

```bash
swayam sim                       # list scenarios
swayam sim capture_weekly_tasks  # replay one
swayam ask "add a task to send the Q3 report due friday"
swayam tasks                     # current state, from the projection
swayam log                       # the event log, in append order
```

State is in-memory in Phase 1 and does not survive between invocations. The CLI says so
rather than letting `swayam tasks` look like it lost your data.

---

## How it is put together

```
utterance → router (tier 0) → planner → domain invariants → event log → projection → reply
              cheap, deterministic   typed proposal    pure validation    append-only
```

Four properties this codebase is built to keep:

1. **The event log is the only source of truth.** State is a derived view, rebuildable
   by replay. No component writes a projection directly.
2. **The model never writes to the event log.** A planner emits a `ProposedChange` — a
   plain data structure with no side effects — which the domain validates like any
   other command. A malformed plan is a validation failure, not a corrupted aggregate.
3. **`domain/` is pure.** No database, no clock, no I/O. The repositories are the
   single sanctioned bridge and live in `storage/`, which is what makes the purity claim
   checkable rather than aspirational.
4. **Uncertainty is surfaced, not resolved by guessing.** Ambiguous references and
   low-confidence routes produce a question. Every answer carries citations to the state
   that produced it.

`make test` includes an architecture suite that enforces 1–3 by parsing the import graph,
and a tripwire that no Phase 1 module can reach across the reserved health boundary.

### Layout

| Path | Holds |
|---|---|
| `src/swayam/core/` | ids, clocks, errors. Imports nothing. |
| `src/swayam/events/` | event envelope, registry, Phase 1 payload schemas |
| `src/swayam/domain/` | aggregates, commands, invariants. Pure. |
| `src/swayam/storage/` | event log adapters, projections, repositories |
| `src/swayam/reasoning/` | router, planner, composer; fast/deep paths land M3/M4 |
| `src/swayam/conversation/` | turn orchestration — wiring, no decisions |
| `src/swayam/llm/` | structurally-typed port + deterministic mock |
| `docs/phase1_arch.md` | the TDD: constraints, event log, reasoning architecture |
| `docs/adr/` | irreversible decisions, and why |

Directories for unbuilt work carry a `README.md` explaining what lands there and which
milestone gates it. None contain a fake implementation.

---

## Where this is going

| Phase | Scope |
|---|---|
| **1** | Temporal + tasks, conversational text. *In progress.* |
| 2 | Feedback capture — where cross-domain reasoning first becomes visible |
| 3 | Voice, layered onto a stable reasoning backend |
| 4 | Health & wellness, read-only trends before any correlation claims |
| 5 | Full synthesis — the system surfaces patterns unprompted |

Phase 1 exists to answer one question from the framework: *what is the minimum viable
cross-domain insight that proves the thesis without health data?*

**Commitment reconciliation.** "What did I say I'd do this week?" vs. "what actually
happened" vs. "what's ahead" — using only temporal and task data. If those hold up in
conversational use, the thesis is validated with the lowest-risk data available, and
everything else in the framework is an extension of the same machinery.
