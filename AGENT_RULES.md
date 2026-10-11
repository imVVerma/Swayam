# AGENT_RULES.md — Rules for the Next Agent (Read This First)

---

## 8. Invariants You Must Not Break

1. **Model never writes events directly.** All mutations go: ProposedChange → Domain validation → EventStore append.
2. **Never silently drop or guess user input.** Ask about the actually-missing thing.
3. **Never ask "which task?" when you already have a target.** Ask about the actually-missing field.
4. **Never edit a test to match broken behaviour.** Fix the code; update fixtures only when behaviour is intentionally changed.
5. **Never mark anything done without raw probe output.** Every fix report must include raw command + output.
6. **No silent fallbacks.** Every degradation must be user-visible.
7. **Deterministic router is the floor.** LLM is fallback only; deterministic path must work fully.

---

## 9. Boundaries for You

| Boundary | Rule |
|----------|------|
| **Scope** | Only F1–F5 (bare times, multi-statement splitting, week view, silent fallback + OpenRouter adapter, malformed_date fixture). No refactors, no new features, no new modules. |
| **Domain/Event Store** | Do not touch `domain/`, `events/`, `storage/eventstore/` unless a listed fix requires it. Explain why if so. |
| **Architecture Tests** | Do not modify `tests/arch/` or `tests/contract/` unless a fix requires it. |
| **Branching** | Work on `handoff/fixes` branch. Commit small and often. |
| **Reporting** | After each fix: what changed, test counts, raw probe outputs, decisions needing me. |
| **Testing** | Run `make test-all`, `make lint`, `make typecheck` after each fix. Do not skip. |
| **Verification** | You report; I verify. Do not self-sign-off. |

---

*Read HANDOFF.md for full context. This file contains only the rules you must follow.*