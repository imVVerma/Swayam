# evals/fixtures/

Recorded conversations with expected intents, mutations and citations. Currently the
scenarios live in `src/swayam/interfaces/cli/scenarios.py`, which keeps them importable
by the test suite and runnable from the CLI; this directory is where recorded
transcripts and model responses land once a real provider is wired in (M2+).

Gates measured, not vibes:

| Metric | Why it matters |
|---|---|
| intent accuracy | the route decision sets the latency budget |
| entity resolution accuracy | wrong task = wrong commitment |
| mutation validity | no invalid proposal may ever commit |
| citation validity | constraint C10 — grounding is not optional |
| clarification rate | too high is annoying, too low is overconfident |
| end-to-end latency | must hold against the tier budgets |
