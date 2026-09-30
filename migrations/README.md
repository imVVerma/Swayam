# migrations/

Empty until the Postgres adapter lands (M3). The event table DDL is specified in
`docs/phase1_arch.md` section 5.1 and is written to be adopted as-is.

The application role must be granted `INSERT` and `SELECT` on the event table and
**never** `UPDATE` or `DELETE`. Enforced by privilege, not by convention — the
append-only guarantee is the one property the whole system is built on, and it should
not depend on nobody writing the wrong query.
