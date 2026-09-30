# privacy/ — unbuilt (M5)

Consent registry, export, erasure, retention.

Phase 1 data is low-risk, but the guarantees have to be established here or never.
The framework's portability and deletion requirements (section 5) are only credible
if they are proven on data that is easy to delete.

Scope: consent keyed by data category, default-deny, revocable; export of the full
event log; erasure that cascades to projections and embeddings; a visible, configurable
transcript retention default.

`health` and `feedback` are registered as permanently unavailable in Phase 1, so
introducing them in Phase 2/4 is an explicit act rather than a default.
