"""Minimal ``.env`` reader.

Deliberately not python-dotenv: the only thing needed here is ``KEY=value`` pairs from
one file, and a dependency is not worth it for a credential read. Rules, matching what a
shell does closely enough:

* blank lines and ``#`` comments are skipped
* an optional ``export `` prefix is dropped
* keys and values are stripped of surrounding whitespace
* matching surrounding quotes are stripped, and ``\\n`` inside double quotes unescaped
* an empty value is allowed; a line with no ``=`` is ignored rather than raising

This never overwrites a variable already present in the real environment, so a CI secret
wins over a checked-out ``.env``.
"""

from __future__ import annotations

import os
from pathlib import Path

_QUOTES = ("'", '"')


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``.env`` content into a mapping. Never raises on a malformed line."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace("\\n", "\n")
        values[key] = value
    return values


def load_dotenv(path: Path | str = ".env", *, override: bool = False) -> dict[str, str]:
    """Merge a ``.env`` file into ``os.environ``; return only what it contributed.

    Returns the parsed values regardless of whether they were written, so callers can
    report what the file said even when the environment already supplied the real value.
    """
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    values = parse_env_file(file_path.read_text(encoding="utf-8"))
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values
