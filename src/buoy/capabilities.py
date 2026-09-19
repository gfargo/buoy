"""Capability/health probing — detects which optional subsystems this host
actually has access to (docker socket, nsenter, smartctl, /proc, /sys) so
``/api/health`` can explain *why* a panel is empty instead of just being
empty.

Every probe is best-effort and never raises: an unexpected failure reports
``unavailable`` rather than propagating, since this runs on a background
loop feeding an unauthenticated, always-200 endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import platform
from typing import TYPE_CHECKING, Any

from buoy.smartctl import run_smartctl
from buoy.subprocess_utils import communicate

if TYPE_CHECKING:
    from buoy.collectors.docker import DockerCollector
    from buoy.collectors.gpu import GpuCollector
    from buoy.config import BuoyConfig

logger = logging.getLogger("buoy.capabilities")


def _demo_snapshot() -> dict[str, dict[str, Any]]:
    """Synthetic all-ok map for --demo. No I/O, ever."""
    return {
        "docker": {"status": "ok", "impact": ""},
        "nsenter": {"status": "not_applicable", "impact": ""},
        "smartctl": {"status": "ok", "impact": ""},
        "proc": {"status": "ok", "impact": ""},
        "sys_thermal": {"status": "ok", "impact": ""},
    }


_DOCKER_UNAVAILABLE = {
    "status": "unavailable",
    "impact": "service discovery, container stats/logs/restart unavailable",
}


async def _probe_docker(docker_collector: Any) -> dict[str, Any]:
    is_available = getattr(docker_collector, "is_available", None)
    if is_available is None:
        return dict(_DOCKER_UNAVAILABLE)
    try:
        ok = await is_available(force=True)
    except Exception:
        logger.debug("capability probe: docker check failed", exc_info=True)
        ok = False
    return {"status": "ok", "impact": ""} if ok else dict(_DOCKER_UNAVAILABLE)


async def _probe_nsenter(disk_collector: Any) -> dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "nsenter",
            "-t",
            "1",
            "-m",
            "--",
            "true",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        _, _ = await communicate(proc, timeout=3)
        if proc.returncode == 0:
            return {"status": "ok", "impact": ""}
    except (TimeoutError, FileNotFoundError):
        pass
    except Exception:
        logger.debug("capability probe: nsenter check failed", exc_info=True)

    # A native (non-container) install never needs nsenter — its local
    # /proc/mounts fallback already sees the real host mounts, so a red
    # "unavailable" there would be a false alarm.
    local_mounts = getattr(disk_collector, "_local_mounts", None)
    if platform.system() == "Linux" and local_mounts is not None:
        try:
            if local_mounts():
                return {"status": "not_applicable", "impact": ""}
        except Exception:
            logger.debug("capability probe: local mount fallback check failed", exc_info=True)

    return {
        "status": "unavailable",
        "impact": "host mount/process visibility unavailable, falls back to container view",
    }


async def _probe_smartctl() -> dict[str, Any]:
    try:
        output = await run_smartctl("--version")
    except Exception:
        logger.debug("capability probe: smartctl check failed", exc_info=True)
        output = None
    if output:
        return {"status": "ok", "impact": ""}
    return {
        "status": "unavailable",
        "impact": "NVMe SMART data and the smart_disk plugin unavailable",
    }


async def _probe_proc() -> dict[str, Any]:
    if platform.system() != "Linux":
        return {"status": "not_applicable", "impact": "CPU/memory metrics unavailable"}
    try:
        with open("/proc/stat"):
            pass
        with open("/proc/meminfo"):
            pass
        return {"status": "ok", "impact": ""}
    except OSError:
        return {"status": "unavailable", "impact": "CPU/memory metrics unavailable"}


async def _probe_sys_thermal() -> dict[str, Any]:
    if platform.system() != "Linux":
        return {"status": "not_applicable", "impact": "temperature gauge unavailable"}
    import os

    for base in ("/sys/class/hwmon", "/sys/class/thermal"):
        try:
            if os.listdir(base):
                return {"status": "ok", "impact": ""}
        except OSError:
            continue
    return {"status": "unavailable", "impact": "temperature gauge unavailable"}


async def _probe_gpu(gpu_collector: Any) -> dict[str, Any] | None:
    """Report GPU capability from the collector's cached detection state.

    ``features.gpu`` defaults to on and is a no-op without a GPU, so most
    hosts have a ``GpuCollector`` but no actual GPU — that must read as
    ``not_applicable``, not a degradation. ``_available`` is ``None`` until
    the collector's first stats-endpoint probe (this function never probes
    itself), which is also not_applicable rather than a false "unavailable".
    """
    if gpu_collector is None:
        return None
    available = getattr(gpu_collector, "_available", None)
    if available is True:
        return {"status": "ok", "impact": ""}
    return {"status": "not_applicable", "impact": ""}


async def probe(
    config: BuoyConfig,
    *,
    docker_collector: DockerCollector | None = None,
    disk_collector: Any = None,
    gpu_collector: GpuCollector | None = None,
) -> dict[str, Any]:
    """Return a snapshot of subsystem capability/health.

    Called from a background loop, never inline in a request handler — some
    probes (docker info, nsenter, smartctl) can take several seconds and
    would blow past a kubelet probe's timeout if run per-request.
    """
    if config.features.demo_mode:
        return _demo_snapshot()

    docker, nsenter, smartctl, proc, sys_thermal, gpu = await asyncio.gather(
        _probe_docker(docker_collector),
        _probe_nsenter(disk_collector),
        _probe_smartctl(),
        _probe_proc(),
        _probe_sys_thermal(),
        _probe_gpu(gpu_collector),
    )

    result = {
        "docker": docker,
        "nsenter": nsenter,
        "smartctl": smartctl,
        "proc": proc,
        "sys_thermal": sys_thermal,
    }
    if gpu is not None:
        result["gpu"] = gpu
    return result
