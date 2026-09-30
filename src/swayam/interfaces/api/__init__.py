"""HTTP interface.

Not built in M0-M2. The seam is fixed by ``TurnOrchestrator.handle``: any surface is a
thin adapter over one call that takes an utterance and returns a TurnResult. The
framework's phase-1 status strip (section 9) is a client of this, not of the domain.
"""
