# HANDOFF.md — Swayam Project Handoff for Next Agent

**Project:** Swayam — Personal Intelligence Platform
**Date:** 2026-10-11
**Tag:** pre-handoff
**Branch for next agent:** `handoff/fixes`

---

## 1. Purpose and Vision

Swayam is a **personal intelligence platform** — a conversational interface to your own commitments, built on an event-sourced core.

**Current scope (text-only CLI v1):**
- Capture tasks and events from natural language
- Query tasks/events (today, this week, overdue, upcoming, completed, clashes)
- Undo last action
- Persistent file-backed storage (JSONL)
- Hybrid understanding: deterministic rules first, LLM fallback only when rules fail

**Explicitly deferred (not in current scope):**
- GUI (decision after dogfood week)
- Health data module
- Feedback/retrieval modules
- Cross-domain synthesis (M3 query paths)
- Live LLM provider (OpenRouter adapter not written)

**Original five-module vision (phased):**
1. **Temporal awareness** — tasks, events, queries (current)
2. **Feedback loops** — retrospectives, calibration
3. **Health** — sleep, activity, recovery signals
3. **Retrieval** — semantic search over history
4. **Conversational layer** — the interface you talk to

---

## 2. Architecture in Plain Terms

### Core Idea
Event sourcing: every state change is an immutable event in an append-only log. Aggregates (Task, Project, CalendarEvent) replay their stream to rebuild state. Projections fold the global log into read models for fast queries.

### Turn Pipeline (one conversational turn)
```
Utterance
    → Router (classifies intent, extracts slots: action, title, due_date, target_ref, etc.)
    → Planner (maps instructions → ProposedChange: kind + fields)
    → Domain (validates command against invariants, emits Event)
    → Event Store (appends to stream, enforces optimistic concurrency)
    → Projection (folds event into read models)
    → Reply Composer (human-readable text + clarification if needed)
```

### Key Layers (dependency direction: top → bottom)
| Layer | Responsibility | Key Files |
|-------|---------------|-----------|
| **Interfaces** | CLI, API, scenarios | `interfaces/cli/app.py`, `interfaces/cli/scenarios.py` |
| **Conversation** | TurnOrchestrator, reply, trace | `conversation/turn.py`, `conversation/turn.py` |
| **Reasoning** | Router (deterministic + LLM), Planner (rule + LLM), proposals | `reasoning/router/`, `reasoning/mutation/` |
| **Domain** | Aggregates (Task, Project), commands, invariants | `domain/task.py`, `domain/project.py` |
| **Events** | Event types, envelope, registry, catalogue | `events/catalogue.py`, `events/envelope.py` |
| **Storage** | EventStore port + adapters (InMemory, File, Postgres) | `storage/eventstore/`, `storage/bootstrap.py` |
| **Projections** | TaskCurrentProjection, QueryProjection | `storage/projections/*.py` |
| **LLM Port** | `LLM` protocol + `MockLLM` + (stub) OpenRouter adapter | `llm/port.py`, `llm/mock.py` |
| **Core** | Clock, IDs, errors | `core/clock.py`, `core/ids.py`, `core/errors.py` |

### Hybrid Design (Rules First, LLM Fallback)
- **DeterministicRouter** — regex/keyword rules, zero cost, used by default
- **LLMRouter** — calls `LLM.complete()` via port, falls back to DeterministicRouter on error
- **RuleBasedPlanner** — maps router instructions to ProposedChange via deterministic logic
- **LLMPlanner** — calls LLM, falls back to RuleBasedPlanner on error
- **Runtime** — `build_runtime(reasoning="llm" | "deterministic")` swaps the pair

### File-Backed Store
- `FileEventStore` (JSONL) implements `EventStore` port
- Lazy replay on first access; persists every append
- Path: `~/.local/share/swayam/events.jsonl` (configurable via `SWAYAM_DATA_DIR`)

---

## 3. Decisions and ADRs (One Line Each)

| ID | Decision | Reason |
|----|----------|--------|
| ADR-001 | Event sourcing with aggregates | Immutable audit trail, replayability, invariants enforced at write |
| ADR-002 | Router + Planner as separate protocols | Swappable implementations, testable in isolation |
| ADR-003 | LLM behind a port (`LLM.complete`) | Provider-neutral, testable with `MockLLM`, no vendor lock-in |
| ADR-004 | Hybrid: rules first, LLM fallback | Speed, cost, works offline, LLM only for ambiguous cases |
| ADR-005 | File-backed store (JSONL) for v1 | Zero deps, portable, easy to inspect/debug |
| ADR-006 | IST clock as default | User is in India; all date parsing assumes DD/MM default |
| ADR-007 | Model never writes events directly | All mutations go through domain validation |
| ADR-008 | `task.undone` compensating event for undo | Append-only log, no mutation of past events |
| ADR-009 | IST timezone for dates | User context is India; DD/MM default for ambiguous dates |
| ADR-010 | Eval gate uses MockLLM only | Reproducible CI, no external dependencies |
| ADR-011 | `make eval` only; `make eval-live` is stub | No live credentials in CI; live eval manual |
| ADR-012 | No silent drops or guesses | Ask about the actually-missing thing |

---

## 4. Current State by Checkpoint

| Checkpoint | Status | Notes |
|------------|--------|-------|
| **CP0 — Hygiene** | ✅ Done | 2 eval fixtures fixed (malformed_date, overconfidence_trap); lint/typecheck clean; Makefile uses `.venv/bin` |
| **CP1 — Usable Shell** | ✅ Done | File-backed store, IST clock, `swayam chat`, `undo` command |
| **CP2 — Input Robustness** | ⚠️ Partial | Fuzzy matching works; date formats (DD/MM/YYYY, ordinals, next week, end of month) work; **bare times broken** (see bugs) |
| **CP3 — Events & Timed Items** | ⚠️ Partial | Event aggregate exists, due_time on tasks, event vs task routing; **time parsing broken for bare times** |
| **CP4 — Queries** | ✅ Done | `today`, `week`, `overdue`, `upcoming`, `completed`, `events`, `clashes`; week view has boundary bug |
| **CP5 — Eval Suite** | ⚠️ Partial | 34 fixtures, `make eval` works (MockLLM); `make eval-live` is **stub only**; no OpenRouter adapter |
| **CP6 — Dogfood** | ❌ Not Started | No week of real usage; `misses.md` and 4 "new" fixtures are seed fixtures only |

---

## 5. Verified vs Claimed Ledger

| Capability | Verified? | Proof Command |
|------------|-----------|---------------|
| File-backed persistence | ✅ | `swayam ask "add a task to test"` → `swayam tasks` after restart |
| IST clock default | ✅ | `swayam today` shows IST date |
| `swayam chat` loop | ✅ | `swayam chat` → interactive, `exit`/`quit` works |
| `undo` command | ✅ | `swayam ask "add a task to test"` → `swayam undo` → task gone |
| Deterministic date parsing (DD/MM/YYYY, ordinals, next week, end of month) | ✅ | `swayam ask "schedule meeting for 15/10/2026"` → due 2026-10-15 |
| Fuzzy matching (verbs, task names) | ✅ | `swayam ask "remid me to test"` → asks clarification |
| Duplicate detection | ✅ | `swayam ask "add a task to test"` twice → asks to update |
| Event creation (with time range, location, kind) | ✅ | `swayam ask "exam tomorrow 10:30 to 11:30 in room 1001"` |
| Task with due_time | ✅ | `swayam ask "submit assignment by friday 10pm"` |
| Query: `swayam today` | ✅ | Shows tasks due today + events today |
| Query: `swayam week` | ⚠️ Boundary bug | Event on Monday shows in `upcoming` but not `week` |
| Query: `swayam overdue` | ✅ | Shows overdue tasks with days count |
| Query: `swayam upcoming --days 7` | ✅ | Shows tasks + events |
| Query: `swayam completed` | ✅ | Shows recently completed tasks |
| Query: `swayam events` | ✅ | Lists all events with details |
| Query: `swayam clashes` | ✅ | Detects overlapping events |
| `make eval` (MockLLM) | ✅ | 34/34 pass |
| `make lint` / `make typecheck` | ✅ | Clean |
| **Bare times** (`5 pm`, `10:30 am`, `10 o'clock at night`, `exam 10:30 to 11:30`) | ❌ **Broken** | All ask "When does the event start?" |
| Multi-statement paragraph → multiple events | ❌ **Broken** | Merges into one wrong event |
| Silent fallback on missing key | ❌ | No user-facing message |
| `make eval-live` (OpenRouter) | ❌ | Stub only; no adapter written |
| Dogfood week | ❌ | Not run; `misses.md` is fabricated |

---

## 6. Known Bugs with Exact Repro Inputs

### B1: Bare Times — Wrong Clarification
```bash
swayam ask "meeting at 5 pm"
swayam ask "meeting at 10:30 am"
swayam ask "meeting at 10 o'clock at night"
swayam ask "exam 10:30 to 11:30"
```
**Expected:** Create event with correct time.
**Actual:** Clarification "When does the event start?"
**Root cause:** `create_event` path in router sets `action: "create_event"` but planner requires `starts_at`/`ends_at`; bare times not converted to starts_at/ends_at in router.

### B2: Multi-Statement Paragraph → One Wrong Event
```bash
swayam ask "exam tomorrow 10:30 to 11:30 in room 1001; quiz at 5 tomorrow; assignment due Saturday 10pm"
```
**Expected:** 3 events (exam, quiz, assignment task).
**Actual:** 1 event with entire paragraph as title, kind=test, wrong time.
**Root cause:** Router doesn't split on `;` for events; clause splitting only for mutations.

### B3: Week View Boundary Bug
```bash
# Event on Monday 2026-10-12
swayam week   # Shows nothing
swayam upcoming --days 7  # Shows the event
```
**Expected:** Week view (Mon-Sun from today) shows event.
**Actual:** Week view uses `today.weekday()` but event projection may use different boundary.

### B4: Silent Fallback on Missing LLM Key
```bash
# With reasoning="llm" but no OPENROUTER_API_KEY
swayam ask "schedule the meeting"
```
**Expected:** Clear message "LLM unavailable, using rules."
**Actual:** Silently falls back to deterministic router; user sees no indication.

### B5: Malformed Date Fixture Changed to "create" — Needs Re-check
```yaml
# tests/evals/fixtures/malformed_date.yaml
# Original: action: "reschedule", target_ref: "budget report"
# Current:  action: "create", title: "meeting"
```
**Issue:** Fixture was changed to make tests pass; original intent was to test malformed date on existing task. Now tests "create" with no date — different scenario.

---

## 7. Fix List (In Order)

### F1: Bare Times — Accept "5 pm", "10:30 am", "10 o'clock at night", "10:30 to 11:30"
**Files to touch:** `reasoning/router/dates.py` (extract_time, extract_time_range), `reasoning/router/deterministic.py` (router), `reasoning/mutation/rule_planner.py` (planner)
**Acceptance:**
- `swayam ask "meeting at 5 pm"` → event at 17:00
- `swayam ask "meeting at 10:30 am"` → event at 10:30
- `swayam ask "meeting at 10 o'clock at night"` → event at 22:00
- `swayam ask "exam 10:30 to 11:30"` → event 10:30–11:30
- All existing tests pass

### F2: Multi-Statement Splitting for Events
**Files:** `reasoning/router/deterministic.py` (clause splitting), `conversation/turn.py` (plan-gap for events)
**Acceptance:**
- `swayam ask "exam tomorrow 10:30 to 11:30; quiz at 5 tomorrow"` → 2 events
- Plan-gap reported if only 1 of 2 lands
- Existing clause-splitting tests pass

### F3: Week View — Define "Week" as Next 7 Days from Today
**Files:** `storage/projections/query.py` (events_this_week, tasks_this_week)
**Acceptance:**
- Event on Monday → `swayam week` shows it (if today ≤ Monday ≤ today+6)
- Consistent with `upcoming --days 7`

### F4: Silent Fallback + Real OpenRouter Adapter
**Files:** `llm/port.py` (protocol), new `llm/openrouter.py`, `llm/env.py`, `runtime.py` (fallback message)
**Acceptance:**
- Missing key → user sees "LLM unavailable, using rules."
- `OPENROUTER_API_KEY` + `OPENROUTER_MODEL` in `.env` → real calls work
- `make eval-live` runs against OpenRouter (manual, not in CI)

### F5: Re-check malformed_date Fixture
**Files:** `tests/evals/fixtures/malformed_date.yaml`
**Acceptance:**
- Fixture tests original intent (malformed date on existing task) OR is explicitly re-scoped
- Test passes with current behavior

---

## 8. Invariants the Next Agent Must Not Break

1. **Model never writes events directly.** All mutations go: ProposedChange → Domain validation → EventStore append.
2. **Never silently drop or guess user input.** Ask about the actually-missing thing.
3. **Never ask "which task?" when you already have a target.** Ask about the actually-missing field.
4. **Never edit a test to match broken behaviour.** Fix the code; update fixtures only when behaviour is intentionally changed.
5. **Never mark anything done without raw probe output.** Every fix report must include raw command + output.
6. **No silent fallbacks.** Every degradation must be user-visible.
7. **Deterministic router is the floor.** LLM is fallback only; deterministic path must work fully.

---

## 9. Boundaries for the Next Agent

| Boundary | Rule |
|----------|------|
| **Scope** | Only F1–F5 above. No refactors, no new features, no new modules. |
| **Domain/Event Store** | Do not touch `domain/`, `events/`, `storage/eventstore/` unless a listed fix requires it. Explain why if so. |
| **Architecture Tests** | Do not modify `tests/arch/` or `tests/contract/` unless a fix requires it. |
| **Branching** | Work on `handoff/fixes` branch. Commit small and often. |
| **Reporting** | After each fix: what changed, test counts, raw probe outputs, decisions needing me. |
| **Testing** | Run `make test-all`, `make lint`, `make typecheck` after each fix. Do not skip. |
| **Verification** | You report; I verify. Do not self-sign-off. |

---

## 10. How to Run Everything

### Environment
```bash
# WSL / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Make Targets
```bash
make test          # unit, contract, arch, integration
make test-all      # everything including evals
make eval          # pytest tests/evals/ -v (MockLLM)
make eval-live     # stub (requires OpenRouter adapter)
make lint
make typecheck
make sim           # scenario replay
make clean
```

### Probe Harness (Dictated Test Paragraph)
```bash
# As separate sentences
swayam ask "exam tomorrow 10:30 to 11:30 in room 1001 Bharti Block"
swayam ask "quiz at 5 tomorrow"
swayam ask "assignment due Saturday 3 October 10pm IST"
swayam ask "conference 31 Oct to 2 Nov"
swayam ask "friend's birthday 16 October"
swayam ask "book train tickets for 19 October and return for 23 October"

# As one block (tests multi-statement)
swayam ask "exam tomorrow 10:30 to 11:30 in room 1001 Bharti Block; quiz at 5 tomorrow; assignment due Saturday 3 October 10pm IST; ship a conference video; conference 31 Oct–2 Nov; friend's birthday 16 October; book train tickets for 19 October and return for 23 October; an assignment due 9am that is already done"
```

### Environment
```bash
# .env (for live eval later)
OPENROUTER_API_KEY=sk-...
OPENROUTER_MODEL=openrouter/auto
SWAYAM_DATA_DIR=~/.local/share/swayam  # optional
```

---

## 11. Git Operations Before Switch

```bash
# Commit everything
git add -A
git commit -m "pre-handoff: CP0-CP5 complete, CP6 not started, bugs documented"

# Tag
git tag pre-handoff

# Create branch for next agent
git checkout -b handoff/fixes

# Push if remote exists
# git push origin handoff/fixes
```

---

*End of HANDOFF.md*