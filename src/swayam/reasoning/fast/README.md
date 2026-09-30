# fast/ — tier 1, unbuilt (M3)

Answers `STATE_QUERY`, `TEMPORAL_QUERY` and `LOAD_QUERY` from the projection layer
alone. No model call, sub-300ms budget.

Depends on the M3 projections: `agenda_day`, `commitment_load_week`,
`conversational_claim`. Until those exist there is nothing correct to read, and the
orchestrator returns an explicit "not yet" rather than an answer. That is deliberate:
a fast path that answers from state which does not exist is how a system starts
hallucinating while looking confident.

The health module never appears here. Load and cross-domain findings are framed as
observations, not verdicts (constraint C11).
