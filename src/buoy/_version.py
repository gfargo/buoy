"""Resolve Buoy's application version from its canonical package metadata."""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from pathlib import Path

_DISTRIBUTION_NAME = "buoy"
_SOURCE_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
UNKNOWN_VERSION = "0+unknown"


def _read_source_version(pyproject_path: Path) -> str | None:
    """Read ``project.version`` when running from a source checkout."""
    try:
        with pyproject_path.open("rb") as pyproject_file:
            value = tomllib.load(pyproject_file)["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return None
    return value if isinstance(value, str) and value else None


def _resolve_version(source_pyproject: Path = _SOURCE_PYPROJECT) -> str:
    """Prefer checkout metadata, then installed distribution metadata."""
    source_version = _read_source_version(source_pyproject)
    if source_version is not None:
        return source_version

    try:
        return _distribution_version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return UNKNOWN_VERSION


VERSION = _resolve_version()
