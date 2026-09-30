"""Model access: a structurally-typed port plus a deterministic mock.

Phase 1 ships against the mock. A real provider adapter slots in behind the same
contract without changing anything above it.
"""

from swayam.llm.mock import MockLLM
from swayam.llm.port import LLM

__all__ = ["LLM", "MockLLM"]
