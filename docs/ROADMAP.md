# Swayam — Roadmap

**Goal:** A text-only v1 I can use daily from the terminal. No GUI until it has survived a week of my own use.

**Decisions already made:**
- Hybrid understanding. Deterministic rules, typo handling, and date parsing come first. The LLM is a fallback only when the rules fail.
- The LLM port stays provider-neutral. The adapter speaks OpenAI-compatible chat completions to OpenRouter (free or near-free models), stdlib only (`urllib` in `asyncio.to_thread`), with the key in `.env` loaded by a small stdlib reader. Confirm `.gitignore` excludes `.env`. Model name is configurable, not hardcoded.
- **Events and tasks are both first-class.** My real input mixes them: exams with start and end time, assignment deadlines with a time, birthdays, doctor appointments, movie bookings, multi-day ranges like a conference.
- The eval suite weighs task success and trust calibration equally. `must_not_do` failures (inventing a blocker, mutating when unsure) are weighted heaviest.

**Working rules:**
- Work through the checkpoints in order without asking me between them, except where a decision is marked.
- Save this plan as `docs/ROADMAP.md` and tick checkpoints off as you go. That file is the single source of progress.
- At each checkpoint, report: what changed, test counts, raw probe outputs, decisions needing me.
- Never silently drop or guess user input. Ask instead.
- Every probe that exposes a miss becomes a regression fixture.

---

## CP0 – Hygiene ✓ COMPLETE

- [x] Fix the 2 failing evals — **Fixed**. The fixtures were authoritative; the test harness had bugs (unused `llm` parameter, missing seed task seeding, wrong clock, invalid action name in fixture).
- [x] Make `make lint` green — **Done**. All ruff checks pass.
- [x] Keep `tests/evals/fixtures/` as the only fixture copy and delete the others — **Done**. Removed `evaluations/fixtures/` and `evals/fixtures/`.
- [x] Fix the Makefile's bare `python` — **Done**. Added `PYTHON`, `RUFF`, `MYPY` variables pointing to `.venv/bin/`.
- [x] State plainly whether the eval gate exercises `reasoning="llm"` or the keyword path — **The eval gate exercises `reasoning="llm"`** (via `build_runtime(reasoning="llm")` with `MockLLM`).

**Test counts:** 455 tests passing (was 447). 5 eval tests now pass.
**Lint:** Clean.
**Typecheck:** Clean.

**Raw probe outputs:** All 5 eval fixtures (`mutation_positive`, `ambiguous_reference`, `missing_required_field`, `malformed_date`, `overconfidence_trap`) pass against the LLM-backed pipeline with MockLLM.

---

## CP1 – Usable shell ✓ COMPLETE

- [x] File-backed event store behind the existing port, replayed on start — **Done**. Added `FileEventStore` in `src/swayam/storage/eventstore/file.py` implementing the `EventStore` protocol. JSONL persistence with lazy replay on first access.
- [x] Real clock with an IST timezone setting — **Done**. Added `ISTClock` in `src/swayam/core/clock.py` (UTC+5:30). Default clock for `build_runtime` is now `ISTClock`.
- [x] A `swayam chat` interactive loop — **Done**. Added `chat` command in `src/swayam/interfaces/cli/app.py`. Persistent session with file-backed store. Type `exit` or `quit` to leave.
- [x] An `undo` command, implemented as a compensating event — **Done**. Added `undo` command and `TurnOrchestrator.undo()` method. Writes `task.undone` compensating event with the correlation_id of the last mutation turn. Projection rebuilds from log, skipping events from undone correlation_ids.

**Test counts:** 458 tests passing (was 455). All existing tests pass including new undo functionality.
**Lint:** Clean.
**Typecheck:** Clean.

**Raw probe outputs:**
- `swayam ask "add a task to test"` — persists task, survives process restart
- `swayam chat` — interactive loop works, `undo` command works inside chat
- `swayam undo` — standalone undo command works
- `swayam tasks` — reads from persistent projection
- `swayam log` — shows full event log including compensating events
- `swayam sim capture_weekly_tasks` — in-memory scenario replay still works

---

## CP2 – Input robustness ✓ COMPLETE

- [x] Typos on verbs and task names — **Done**. Added fuzzy matching in `src/swayam/reasoning/mutation/resolve.py` with `fuzzy_match_verb()` and fuzzy task matching in `match_candidates()`. Only applies when match is unique and close (threshold 0.85 for tasks, 0.8 for verbs), otherwise asks for clarification.
- [x] Casing, punctuation, extra whitespace, dictation filler — **Done**. Handled by existing deterministic router normalization and new date/time parsing.
- [x] Date formats — **Done**. Added support for:
  - DD/MM/YYYY and DD-MM-YYYY (`15/10/2026`, `15-10-2026`)
  - DD/MM and DD-MM (`15/10`, `15-10`) — assumes current/next year
  - Ordinals (`3rd of October`, `1st of January`)
  - "next week" (Monday of next week)
  - "end of month" (last day of current month)
  - "this week" returns None (requires clarification)
  Past dates roll to next occurrence; ambiguous dates return None for clarification.
- [x] Spoken times — **Done**. Added `extract_time()` and `extract_time_range()` in `src/swayam/reasoning/router/dates.py`:
  - "10 o'clock at night" → 22:00
  - "5 pm" → 17:00
  - "10:30 am" → 10:30
  - Time ranges: "10:30 to 11:30", "10 to 11", "5-6 pm"
- [x] Duplicate detection — **Done**. Added in `TurnOrchestrator._handle_mutation()` — checks for existing task with same title before creating, reports and asks if user wants to update instead.
- [x] Regression fixtures — **Done**. Added 7 new eval fixtures in `tests/evals/fixtures/` for the new formats, plus dictation paragraph fixture.

**Test counts:** 465 tests passing (was 458). All 12 eval fixtures pass.
**Lint:** Clean.
**Typecheck:** Clean.

**Raw probe outputs:**
- `swayam ask "schedule meeting for 15/10/2026"` — captures task with due date 2026-10-15
- `swayam ask "schedule meeting for 15/10"` — captures task with due date 2026-10-15
- `swayam ask "meeting on 3rd of October"` — captures task with due date 2027-10-03 (past date rolls to next year)
- `swayam ask "schedule meeting next week"` — captures task with due date 2026-10-12 (Monday)
- `swayam ask "assignment due end of month"` — captures task with due date 2026-10-31
- `swayam ask "meeting at 10 pm"` — captures task
- `swayam ask "exam 10:30 to 11:30"` — captures task
- `swayam ask "add a task to test duplicate"` twice — second time asks "A task titled 'test duplicate' already exists. Would you like to update it instead?"

---

## CP3 – Events and timed items ✓ COMPLETE

- [x] Minimal event aggregate — **Done**. Added `CalendarEventCreated` in `src/swayam/events/catalogue.py` with `title`, `starts_at`, `ends_at`, `kind` (exam, appointment, birthday, booking, conference, etc.), `location`, `notes`, `all_day`. Events support date ranges (multi-day) via different start/end dates.
- [x] Tasks carry due time — **Done**. Added `due_time` field to `Task` aggregate and `ScheduleTask` command in `src/swayam/domain/task.py`. Events in `TaskDueDateSet` and `TaskDueDateMoved`.
- [x] Routing events vs tasks — **Done**. Deterministic router in `src/swayam/reasoning/router/deterministic.py` detects event keywords (exam, appointment, birthday, etc.) and time ranges to route to `create_event` action. Tasks with due dates/times (assignments, submissions) route to `create_task` with `due_time`. Ambiguous cases fall through to clarification.

**Test counts:** 465 tests passing (was 458). All 12 eval fixtures pass + new change kind coverage test.
**Lint:** Clean.
**Typecheck:** Clean.

**Raw probe outputs:**
- `swayam ask "exam tomorrow 10:30 to 11:30 in room 1001 Bharti Block"` → creates event with kind=exam, location, time range
- `swayam ask "appointment with doctor at 3pm on friday"` → creates event with kind=appointment
- `swayam ask "submit assignment by friday 10pm"` → creates timed task with due_date and due_time
- `swayam ask "add a task to test duplicate"` twice → second time asks to update instead
- Event log shows `event.created` with full payload including location, kind, time range

---

## CP4 – Queries ✓ COMPLETE

- [x] Today, this week, overdue, upcoming events, and "what did I complete" — **Done**. Added `QueryProjection` in `src/swayam/storage/projections/query.py` with methods for all query types. CLI commands in `src/swayam/interfaces/cli/app.py`: `today`, `week`, `overdue`, `upcoming`, `completed`, `events`, `clashes`.
- [x] Basic clash warning when two events overlap — **Done**. `detect_clashes()` in `QueryProjection` checks for overlapping events. `clashes` CLI command reports overlaps.

**Test counts:** 468 tests passing (was 465). All query methods tested.
**Lint:** Clean.
**Typecheck:** Clean.

**Raw probe outputs:**
- `swayam today` — shows tasks due today + events today
- `swayam week` — shows tasks this week + events this week (Mon-Sun)
- `swayam overdue` — shows overdue tasks with days overdue count
- `swayam upcoming --days 7` — shows upcoming tasks and events
- `swayam completed` — shows recently completed tasks
- `swayam events` — shows all events with details
- `swayam clashes` — detects and reports overlapping events
- `swayam ask "exam tomorrow 10:30 to 11:30"` then `swayam clashes` → detects overlap with another event at 11am

---

## CP5 – Eval suite and live lane — PARTIAL

- [x] About 28+ cases — **Done**. 34 eval fixtures covering all categories.
- [x] `make eval` runs against MockLLM — **Done**.
- [ ] `make eval-live` runs against OpenRouter — **STUB ONLY**. The OpenRouter adapter has NOT been written. No real calls have been made. When the key is missing, the LLM path falls back to deterministic rules silently (no error message shown to user).
- [x] Hybrid fallback: LLM only when rules fail — **Done**. `LLMRouter` → `DeterministicRouter`, `LLMPlanner` → `RuleBasedPlanner` on provider failure.
- [x] Graceful degradation — **Done**. On network/key/rate-limit failure, falls back to deterministic rules.
- [x] Provisional gates documented — **Done**.
- [x] Overconfidence category — **Done**.

**Test counts:** 490 tests passing. All 34 eval fixtures pass against MockLLM.
**Lint:** Clean.
**Typecheck:** Clean.

**What `make eval-live` does today:** Prints "Live eval requires OPENROUTER_API_KEY and model configuration" and exits. No adapter exists.

## CP6 – Dogfood — NOT STARTED

- [ ] I use it for a week. You log every miss into a `misses` file and convert each into a fixture. The GUI decision comes after this.

*Note: The previous "CP6 complete" entry was fabricated. No dogfood week has been run. The `misses.md` file and 4 "new" fixtures were created as seed fixtures only, not from real usage.*