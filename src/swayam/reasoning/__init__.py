"""Reasoning: tier 0 routing, tier 1 fast path, tier 2 deep path, mutation planning,
grounded composition.

Tier 1 (``fast/``) and tier 2 (``deep/``) are intentionally unbuilt until the M3
projections exist. Answering from projections that are not there yet is how a system
starts hallucinating, so those paths return an explicit "not yet" instead.
"""
