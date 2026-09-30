"""Architecture fitness functions.

The layering in docs/phase1_arch.md section 9 is a claim about the codebase. These
tests are what make it a *checked* claim, and they are the reason the rules can be
trusted when someone under time pressure wants to take a shortcut.

Two properties are enforced:

1. The dependency rule. ``core`` imports nothing, ``domain`` is pure I/O-free logic,
   and the layers only ever point downward.
2. The reserved health boundary. No Phase 1 module may reach a health store, because
   the framework requires health data to be isolated (constraint C6) and the whole
   point of deciding that *now* is so the isolation cannot be undone later by accident.
   This test is a tripwire: it passes trivially until someone adds the first health
   module, at which point it forces a deliberate decision.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "swayam"

# layer -> the layers it is permitted to import (plus itself)
ALLOWED: dict[str, frozenset[str]] = {
    "core": frozenset(),
    "events": frozenset({"core"}),
    "domain": frozenset({"core", "events"}),
    "storage": frozenset({"core", "events", "domain"}),
    "llm": frozenset({"core", "observability"}),
    "observability": frozenset({"core"}),
    "retrieval": frozenset({"core", "events", "domain", "storage"}),
    "reasoning": frozenset({"core", "events", "domain", "storage", "llm", "retrieval"}),
    "privacy": frozenset({"core", "events", "domain", "storage"}),
    "conversation": frozenset(
        {"core", "events", "domain", "storage", "llm", "reasoning", "retrieval",
         "observability", "privacy"}
    ),
    "interfaces": frozenset(
        {"core", "events", "domain", "storage", "llm", "reasoning", "retrieval",
         "conversation", "observability", "privacy"}
    ),
}

# Modules that must never appear in a Phase 1 import graph. See constraint C6.
RESERVED_MODULES = ("swayam.health", "swayam.biometrics", "swayam.voice")


def top_layer(path: Path) -> str:
    relative = path.relative_to(SRC)
    return relative.parts[0] if len(relative.parts) > 1 else "__root__"


def imported_modules(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def swayam_layer(module: str) -> str | None:
    """'swayam.storage.eventstore.port' -> 'storage'; 'swayam.events' -> 'events'.

    Returns ``None`` for anything outside the package, and for the bare package name
    ``swayam`` itself, which sits above every layer.
    """
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "swayam":
        return None
    return parts[1]


def swayam_modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


ALL_MODULES = swayam_modules()


class TestDependencyRule:
    @pytest.mark.parametrize("path", ALL_MODULES, ids=lambda p: p.name)
    def test_imports_only_point_downward(self, path: Path) -> None:
        layer = top_layer(path)
        if layer in ("__root__", "interfaces"):
            return
        permitted = ALLOWED.get(layer)
        assert permitted is not None, f"{layer} is not a declared layer"

        for module in imported_modules(path):
            target = swayam_layer(module)
            if target is None:
                continue
            assert target in permitted | {layer}, (
                f"{path.relative_to(SRC)} ({layer}) may not import from {target}; "
                f"permitted: {sorted(permitted | {layer})}"
            )

    @pytest.mark.parametrize("path", ALL_MODULES, ids=lambda p: p.name)
    def test_core_imports_nothing_from_swayam(self, path: Path) -> None:
        if top_layer(path) != "core":
            return
        # Intra-package re-exports (swayam.core.*) are fine; anything else is not.
        offenders = [
            m
            for m in imported_modules(path)
            if m.split(".")[0] == "swayam" and not m.startswith("swayam.core")
        ]
        assert not offenders, f"core must be the root of the graph, found {offenders}"

    def test_every_layer_is_declared(self) -> None:
        found = {top_layer(p) for p in ALL_MODULES} - {"__root__"}
        assert found <= set(ALLOWED), f"undeclared layers: {found - set(ALLOWED)}"


class TestHealthBoundary:
    """Constraint C6: health data sits in a separately encrypted store, joined only at
    query time. Phase 1 must not be able to reach it -- and must not be tempted into
    a "temporary" shared table that would dissolve the boundary."""

    @pytest.mark.parametrize("path", ALL_MODULES, ids=lambda p: p.name)
    def test_no_phase1_module_imports_a_reserved_store(self, path: Path) -> None:
        for module in imported_modules(path):
            for reserved in RESERVED_MODULES:
                assert not module.startswith(reserved), (
                    f"{path.relative_to(SRC)} imports {module}. The health boundary is "
                    f"reserved: it may only be crossed at query time from retrieval/, "
                    f"with its own store, credentials and encryption key."
                )

    def test_no_health_module_exists_yet(self) -> None:
        assert not (SRC / "health").exists(), (
            "Phase 1 must not contain health code. If you are adding it, that is a "
            "milestone decision, not a code change."
        )

    def test_no_phase1_event_type_carries_health_data(self) -> None:
        from swayam.events.catalogue import registry

        names = registry.names()
        assert not [n for n in names if n.startswith("health.")]
