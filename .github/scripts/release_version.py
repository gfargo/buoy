"""Resolve and verify a release tag against the canonical package version."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

SEMVER_TAG = re.compile(
    r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def normalize_release_tag(tag: str) -> str:
    """Return the v-prefixed semver from a bare or component-prefixed tag."""
    normalized = tag.removeprefix("buoy-")
    if not SEMVER_TAG.fullmatch(normalized):
        raise ValueError(f"Unsupported release tag: {tag}")
    return normalized


def project_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as pyproject_file:
        return tomllib.load(pyproject_file)["project"]["version"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("pyproject", type=Path)
    args = parser.parse_args()

    try:
        normalized = normalize_release_tag(args.tag)
        package_version = project_version(args.pyproject)
    except (OSError, KeyError, tomllib.TOMLDecodeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    tag_version = normalized.removeprefix("v")
    if tag_version != package_version:
        print(
            f"Release tag version {tag_version} does not match package version {package_version}",
            file=sys.stderr,
        )
        return 1

    print(normalized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
