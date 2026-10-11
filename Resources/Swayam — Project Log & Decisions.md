# Swayam — Project Log & Decisions

*Last updated: 30 September 2026 (5th session update)*

## What's built so far (Phase 1, milestones M0–M2)

- Event-sourced core: append-only event log, Task/Project aggregates, invariant rules (I1–I7).
- Rule-based conversation pipeline: deterministic keyword/regex router → rule-based mutation planner → domain validation → event log → projection.
- CLI mock runtime (`swayam ask`, `swayam sim`, `swayam tasks`, `swayam log`).
- 272 tests passing, lint clean, type-check clean. Architecture fitness tests enforce module layering and the reserved health-data boundary.
- Query paths (what's on today, am I overcommitted, how did last week go) are **not built yet** — they return an explicit "not yet" rather than guessing. This is by design, gated to milestone M3.

## What testing on 30 Sept found

Ran a batch of natural, first-person dictated sentences (exam schedule, quiz, assignment, conference, friend's birthday, train tickets) through the live orchestrator, plus a follow-up batch probing edge cases. Findings:

1. **No verb it recognizes → outright rejection**, even when the date was parsed correctly internally. "remind me to...", "I need to...", "submit..." are not in its \~10-word verb list.
2. **Compound sentences silently mangle or drop information.** Two "add a task" clauses in one sentence produced one task with a garbled title. A "mark X done, also move Y" sentence completed X and **silently discarded** the Y instruction with no warning — this breaks the system's own "ask, don't guess" principle.
3. **Titles get polluted with date text** the stripping logic doesn't recognize (e.g. "due in 3 days", "due october 19" ends up baked into the task title alongside the correctly-extracted due date).
4. **Relative/offset time expressions aren't understood** ("push the report by two days" — no date computed, falls through to a clarification instead of doing the math).
5. **Pronoun/reference corrections fail** ("actually make that due Monday instead" — "that" isn't resolved to anything).
6. **A leading clause can break parsing entirely** ("it's urgent, add a task to..." — confidence drops below threshold, silent refusal instead of a clarifying question).
7. **Missing verbs entirely**: "cancel"/"delete" aren't recognized, even though the underlying domain already supports archiving a task.
8. Confirmed earlier: five `ChangeKind` operations (block, archive, rename, reopen, add note) work at the domain/event level but are unreachable from conversation — nothing routes to them yet.

## Decision

- Root cause of items 1–7: intent classification and slot extraction are 100% keyword/regex-based, with zero language understanding. Extending the keyword list further is whack-a-mole against infinite phrasings.
- **Direction chosen:** replace the keyword-matching `DeterministicRouter` / `RuleBasedPlanner` with an LLM-backed implementation behind the existing `LLM` port (`llm/port.py`), which is already scaffolded for structured, schema-validated output. The domain, event store, and projection layers stay untouched — that boundary is the whole point of the port.
- Item 2 (silent partial execution) is treated as a correctness bug regardless of the above, since it violates the system's own core safety guarantee.

## Next steps to execute (superseded — kept for history; see below for current)

1. ~~Wire an LLM-backed `IntentRouter` + `MutationPlanner`~~ — done, see update below.
2. ~~Fix compound-instruction handling~~ — done (turned out to be a resolver bug, not a planning bug — see harness-run update).
3. ~~Fix date-phrase stripping~~ — done.
4. ~~Wire the five unreachable `ChangeKind` operations~~ — done, plus `UNBLOCK_TASK` added.
5. (Longer-term, M3 per the original design doc) Build the query-answering path so the system can report state back, not just take instructions. **Still not started — still the biggest remaining Phase 1 gap.**

## Update — OpenCode's implementation (reported, not yet independently verified)

OpenCode implemented items 1–4 from the prompt above. Notable points from their report:

- **Architectural deviation (recorded as ADR 0011):** `LLM.complete` is async, so the router/planner protocols had to become async too — sync `route()` couldn't honestly call an async model. Cost is two `await`s at the orchestrator call sites; benefit is that `build_runtime(reasoning="llm")` swaps the deterministic pair for the LLM pair with nothing downstream changing.
- **Item 1 (LLM router/planner):** added as `reasoning/router/llm_router.py` and `reasoning/mutation/llm_planner.py`, implementing the same protocols as the deterministic pair (which is unchanged, still used for tests/mock runtime). Structurally prevented from naming a task by internal id, inventing an instruction not traceable to a quoted span of the utterance, or setting a field a given `ChangeKind` doesn't allow.
- **Item 2 (silent partial execution):** enforced in three places — router reports an `instruction_count`, each planner emits at most one proposal per instruction and carries unresolvable references forward instead of dropping them, and the orchestrator reconciles counts and reports any shortfall in the reply plus a `plan_gap` trace entry. Two real bugs were found and fixed in the process, including the projection updating only after the whole apply loop, so a second instruction in the same turn couldn't see what the first had just created.
- **Item 3 (date leakage):** `dates.py` now uses one rule table feeding both extraction and removal, so they can't drift apart. Handles "in 3 days," "october 19," "19 october," "in 6 months." Removal is deliberately conservative (self-evident, preposition-anchored, or trailing-only) so real task titles like "friday standup sync" survive intact.
- **Item 4 (unreachable ChangeKinds):** all 8 wired, plus a new `UNBLOCK_TASK` that wasn't in the original list — reasoning being that shipping `block` without `unblock` would be a one-way door.
- **Two more bugs found by their own tests:** action detection previously scanned the whole clause instead of anchoring to the imperative verb, so "add a note ... that it shipped" was misread as a completion (via the word "shipped") and "add a task to move the boxes" was misread as a reschedule (via "move"). Also: `is_open` was found to incorrectly exclude `BLOCKED` tasks in a test; the test was corrected rather than the domain, since a blocked task is still open/live, just not actionable.
- **Explicitly not done:** no live LLM provider wired in yet — everything above is proven against `MockLLM` only. Wiring a real provider needs a provider + credentials decision, and ADR 0008's eval suite (`tests/evals/`) is still empty and is the stated gate before that happens. Nothing has been committed to git yet.

**Not yet independently verified** — this section is OpenCode's own report. Next session: re-run the same real-sentence probes from 30 Sept against the updated code once the archive is shared, to confirm items 1–3 actually hold end-to-end (not just per their internal tests).

## Update — harness run #1 exposed a severe silent-failure bug (30 Sept, later same day)

Confirmed: no CLI flag exists anywhere; `swayam ask`/`swayam sim` can only ever exercise the deterministic path. The harness I wrote had to reach `reasoning="llm"` directly in Python — which is exactly what surfaced the next finding.

**The critical bug:** `reasoning="llm"` committed **zero events on all 14 test cases** — not errors, not clarifications, just silent no-ops. Root cause: `LLMRouter` had a fallback path that quietly degraded to deterministic behavior when talking to an unregistered `MockLLM`, but `LLMPlanner` had no equivalent fallback and just returned an empty proposal set. Net effect: the "llm" runtime could classify intent but never actually mutate anything, with no visible error — it looked like it was working. There had even been a test that pinned this asymmetry as *intended* behavior; that test was identified as wrong and corrected, and an end-to-end test was added asserting `reasoning="llm"` actually commits events.

**Four more real bugs found via the test-case list itself:**

1. `_detect_action` (and `_propose`) defaulted to `"update"` instead of `None` when no action was detected — so `"book train tickets due october 19"` was silently treated as a reschedule of a task nobody named. Both now default to no action.
2. `resolve_task` normalized the search term but not the stored title, so `"send report"` never matched a task titled `"send the report"`. This was the actual root cause of the Group E (silent-partial-execution) and Group C (unblock) failures — not a multi-instruction planning issue, as the previous round's diagnosis assumed. Worth remembering when re-reading that earlier fix.
3. `MUTATION_VERBS` was a hand-maintained duplicate of `_ACTION_HEADS` and had drifted out of sync — this, not intent understanding, is why "remind me to…" and "I need to…" were being rejected: the dispatcher could parse them but a stale gate blocked them anyway. Same root cause class as the earlier date-extraction/removal drift (two independent lists encoding the same knowledge, edited separately). `_FRAGMENT_VERB` had the same drift and was additionally missing `unblock` entirely, silently dropping fragments that used it.
4. A block-without-reason case asked "Which task?" while already holding the correct task reference — it was asking about the wrong missing field. Now correctly asks "What is it blocked on?"

**Two things that are not bugs, for the record:** `"add a note to the deck that it shipped"` still asks a clarifying question in the harness run — correct, since no task named "the deck" exists in that script's state, not a Group D failure. And this whole run was against `MockLLM`: it proves the fallback wiring works, not model quality — a live provider is still unselected.

409 tests passing, lint/type-check clean, all scenarios pass.

**Worth flagging as a pattern, not just four isolated bugs:** this is the second time a bug has come from two hand-maintained lists encoding the same knowledge and drifting apart (date rules earlier, verb/action lists now). Worth asking OpenCode to audit for other duplicated constant lists across the codebase before they cause a third instance of this.

**Still open:** the harness hasn't actually been re-run yet against the fixed code — everything above is OpenCode's fix report, not independently re-confirmed output.

## Update — priority batch complete, consolidation done (30 Sept, later still)

All 5 items from the master prompt done. 447 tests passing (up from 409), lint/type-check clean, all scenarios pass, harness output unchanged throughout (confirms no regression to the already-verified LLM path).

- **#3 (fragment heads):** fixed properly, not just patched — the hand-written duplicate list was replaced with `_fragment_has_verb()`, which asks the real source of truth directly. A property test now iterates all 41 verbs and asserts each can open a second fragment, acting as a drift *guard* rather than another list to keep in sync.
- **#1 ("not done"):** fix scope grew once precedence was corrected — "not finished/complete/shipped" had the same bug, plus a reference-mangling bug on top. Negation handling consolidated into one place (`resolve_state`). Verified end-to-end: complete → not done → open round-trip.
- **#4 (`_why_no_proposal`):** fixed, but a regression was introduced and self-caught during the fix (reference resolution wasn't being checked first, producing nonsensical clarifying questions). Now derives from `REQUIRED_FIELDS`, itself moved into `proposal.py` next to a new `ACTION_KIND` map that used to be implicit in branch order.
- **#2 (candidate drift):** fixed by extracting one `match_candidates()` both functions build on. New test asserts the list shown to the user is the same list actually considered.
- **Consolidation:** done — `STATE_WORDS` replaces four overlapping completion-word lists, `resolve_state()` replaces two separately-ordered pattern sets, `_looks_like_mutation()` replaces an ad hoc admission tuple, and both `LEADING_VERB` and the LLM router's action literal are now derived from `_ACTION_HEADS` rather than hand-copied.

**Three more latent bugs surfaced purely by doing the derivation** (i.e. found *because* duplication was removed, not by separate testing): `abandon`/`log`/`scrub` were missing from `LEADING_VERB` and could never resolve as references; "closed" was strippable but not recognized as a mutation trigger at all; "push back"/"pull forward" were parsed as bare "push"/"pull", leaving "back the old deck" stuck in the reference text.

**Two judgment calls made without asking, worth being aware of:** the LLM router's action literal needed to derive from both `_ACTION_HEADS` and `STATE_WORDS` together (complete/reopen have no head of their own — they're a refinement of "update" at dispatch time). And a `type: ignore` was left on the runtime-derived `Literal` rather than forcing a type-checker-satisfying workaround that doesn't actually exist — flagged honestly rather than faked.

Untouched as instructed: case-sensitivity precondition, `Expectation("any")`, the unwired fast/deep path split, unread intent constants, eval suite, provider selection. Nothing committed to git yet.

## Current next steps (as of this update)

1. **Eval suite + live provider — decided, see below.**
2. Minor drift-audit leftovers, still deferred, except the date-stripping case-sensitivity fix (cheap, flagged as worth doing "whenever next in that area").
3. **M3 query-answering path** — still the largest functional gap. Substantial enough to be its own branch, not a follow-on commit.
4. ~~Git hygiene~~ — done, `0ab1c8f` on `master`, 79 files, 447 tests passing from the committed state.
5. Frontend — still deferred.

## Decision — eval suite / provider fork (resolved)

- **Credentials:** available now.
- **Provider:** not committed to a specific one — keep the `LLM` port provider-neutral; write a concrete adapter behind it rather than picking a vendor as an architectural decision. Provider choice stays swappable.
- **What the eval suite gates on:** both task success and trust calibration, weighted equally from the start — not success-rate-only. Needs negative cases (ambiguous references, missing fields, unresolvable dates) and a real threshold on clarification quality, per OpenCode's framing, not just "did it do the right thing."
- **Sequencing:** eval suite + adapter work next, ahead of M3 — it's the smaller, more urgent piece (the actual gate on trusting the LLM path at all), and M3 is large enough to deserve undivided attention on its own branch once this is settled.

## Decisions — scope and roadmap (11 Oct 2026)

- **Events and tasks both.** Real input mixes them: exams with start/end times, assignment deadlines with a time, birthdays, doctor appointments, movie bookings. Events become a first-class concept now (CP3), not deferred to M3.
- **Hybrid understanding.** Keyword rules and typo/date handling first; the free-tier LLM only as a fallback when rules fail. Transport is OpenAI-compatible (OpenRouter), stdlib only, key in `.env`.
- **Text-only until robust.** No GUI until after a week of dogfooding.
- **Roadmap (kept by OpenCode in `docs/ROADMAP.md`):** CP0 hygiene (failing evals, lint, fixture triplication, Makefile) → CP1 usable shell (file-backed store, real IST clock, `swayam chat`, undo) → CP2 input robustness (typos, date/time formats, dictation filler, duplicates) → CP3 events → CP4 queries (today, week, overdue, completed) → CP5 eval suite + live lane + hybrid fallback → CP6 one-week dogfood and a misses file.
- **Archaeology review (7 Oct):** 2 of 5 eval tests failing, `make lint` red, eval fixtures in three drifted folders, no persistence, frozen demo clock. All folded into CP0 and CP1.