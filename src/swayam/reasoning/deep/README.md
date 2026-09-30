# deep/ — tier 2, unbuilt (M4)

Asynchronous retrieval-then-synthesis for `CROSS_DOMAIN`. Grounded in
`retrieval/`, cited back to the user.

Two hard requirements, both from the framework:

- **Every claim is cited** to a retrieved unit (constraint C10). An ungrounded answer
  is an eval failure, not a style issue.
- **Observations, not clinical claims** (constraint C11). "You have 11 deadlines this
  month, three more than last month" — never "you are overcommitted".

This is where "how did last week go?" is answered, and it is the first place the
framework's cross-domain thesis becomes visible to a user.
