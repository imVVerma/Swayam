# semantic/ — unbuilt (M3)

Embeddings over task titles/notes, event titles and conversation turns.

Phase 1 default is `pgvector` in a separate Postgres *schema* with a **separate
encryption key**. That satisfies the storage-domain separation in constraint C5
without paying for a second distributed system before we know we need one. If vector
search later needs a dedicated engine, the `SemanticStore` port absorbs the swap.

This is the one place Phase 1 deliberately under-builds for simplicity, and it is the
decision most worth revisiting at the Phase 2 boundary.
