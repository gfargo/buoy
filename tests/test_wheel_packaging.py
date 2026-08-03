"""Regression guard: the built wheel must contain the frontend static assets.

This test builds a wheel from the local source tree and inspects its contents.
If static/ assets are ever dropped from the wheel (e.g. force-include removed
from pyproject.toml), this test fails CI before the broken package ships.

The test is skipped automatically when the build tooling is unavailable (e.g.
in minimal environments that install only runtime deps).
"""

from __future__ import annotations

import glob
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

# Repo root is two levels up from tests/
REPO_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Availability guard
# ---------------------------------------------------------------------------


def _pip_wheel_available() -> bool:
    """Return True if we can build a wheel (hatchling build backend present)."""
    try:
        import importlib.util

        if importlib.util.find_spec("hatchling") is None:
            return False
        result = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--help"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _pip_wheel_available(), reason="pip wheel not available")
def test_wheel_contains_static_assets(tmp_path: Path) -> None:
    """A wheel built from this repo must ship buoy/static/index.html, JS, and CSS."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "-w",
            str(tmp_path),
        ],
        check=True,
        cwd=REPO_ROOT,
    )

    wheels = glob.glob(str(tmp_path / "buoy-*.whl"))
    assert wheels, "No buoy-*.whl produced by pip wheel"

    names = zipfile.ZipFile(wheels[0]).namelist()

    assert "buoy/static/index.html" in names, (
        "buoy/static/index.html missing from wheel — "
        "check [tool.hatch.build.targets.wheel.force-include] in pyproject.toml"
    )
    assert any(n.startswith("buoy/static/js/") for n in names), (
        "buoy/static/js/ files missing from wheel"
    )
    assert any(n.startswith("buoy/static/css/") for n in names), (
        "buoy/static/css/ files missing from wheel"
    )


def test_resolve_static_dir_dev_path_exists() -> None:
    """In a dev / editable install the resolved static dir must contain index.html."""
    from buoy.server import _resolve_static_dir

    static_dir = _resolve_static_dir()
    assert (static_dir / "index.html").exists(), (
        f"_resolve_static_dir() returned {static_dir!r} but index.html not found there"
    )
