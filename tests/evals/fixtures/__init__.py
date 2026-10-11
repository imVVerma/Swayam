"""Fixture loader for the eval suite.

Each fixture is a YAML file in this directory keyed by its ``id`` (the filename minus
``.yaml``).  ``load_fixture`` returns the ``Case`` pydantic model validated against
the fixture data.
"""

from __future__ import annotations

import pathlib

from yaml import safe_load as yaml_load

from tests.evals.spec import Case

_FIXTURE_DIR = pathlib.Path(__file__).parent


def load_fixture(name: str) -> dict:
    """Load fixture ``{name}.yaml`` and coerce into a plain dict."""
    data = yaml_load((_FIXTURE_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    return data


def case(name: str) -> Case:
    """Load a fixture and validate it against the ``Case`` model.

    This is a convenience wrapper; the gate test typically calls
    ``Case.model_validate(load_fixture(name))`` directly.
    """
    return Case.model_validate(load_fixture(name))