# retrieval/ — unbuilt (M3)

The only component permitted to read more than one storage domain (constraint C2).
Joins Layer 1 (structured projections) and Layer 2 (semantic embeddings) at query
time.

It is also the *only* sanctioned crossing point for the reserved health store when
Phase 4 arrives — joined at query time, never merged into a general-purpose store
(constraint C6). `tests/arch/` enforces that no other module can reach across.
