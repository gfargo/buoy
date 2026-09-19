"""Shared pytest fixtures."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest

_PROC_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "proc"

# proc path -> fixture filename under tests/fixtures/proc/
_PROC_FIXTURE_FILES = {
    "/proc/stat": "stat",
    "/proc/meminfo": "meminfo",
    "/proc/cpuinfo": "cpuinfo",
    "/proc/loadavg": "loadavg",
    "/proc/uptime": "uptime",
    "/proc/diskstats": "diskstats",
    "/proc/mounts": "mounts",
}


class FakeFilesystem:
    """Path-dispatched fake for builtins.open() and os.listdir().

    Unlike unittest.mock.mock_open(), which hands every open() call the same
    content regardless of path, this dispatches by the exact path requested —
    required for collectors that read several different /proc or /sys files
    within one call (e.g. SystemCollector._read_cpu_detail reads
    /proc/cpuinfo, then possibly /proc/device-tree/model, then
    /proc/loadavg). Any path not explicitly registered raises
    FileNotFoundError, matching a real host that simply doesn't have that
    file rather than silently falling through to the real filesystem.
    """

    def __init__(self):
        self.files: dict[str, str] = {}
        self.dirs: dict[str, list[str]] = {}

    def set_file(self, path: str, content: str) -> None:
        self.files[str(path)] = content

    def set_dir(self, path: str, names: list[str]) -> None:
        self.dirs[str(path)] = list(names)

    def _open(self, path, *args, **kwargs):
        path = str(path)
        if path in self.files:
            return io.StringIO(self.files[path])
        raise FileNotFoundError(path)

    def _listdir(self, path):
        path = str(path)
        if path in self.dirs:
            return list(self.dirs[path])
        raise FileNotFoundError(path)


@pytest.fixture
def fake_fs():
    """A FakeFilesystem patched over builtins.open and os.listdir for the
    duration of the test. Register paths with fake_fs.set_file()/set_dir()
    before exercising the code under test."""
    fs = FakeFilesystem()
    with (
        patch("builtins.open", side_effect=fs._open),
        patch("os.listdir", side_effect=fs._listdir),
    ):
        yield fs


@pytest.fixture
def fake_procfs(fake_fs):
    """fake_fs pre-seeded from tests/fixtures/proc/* — a realistic /proc
    tree covering stat, meminfo, cpuinfo, loadavg, uptime, diskstats, and
    mounts. Override or add individual paths with fake_fs.set_file() before
    exercising the collector; /sys paths are not seeded, so hwmon/thermal/
    cgroup lookups behave as if absent unless a test adds them."""
    for proc_path, fixture_name in _PROC_FIXTURE_FILES.items():
        fake_fs.set_file(proc_path, (_PROC_FIXTURES_DIR / fixture_name).read_text())
    return fake_fs
