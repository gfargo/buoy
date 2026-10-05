"""GPU collector — NVIDIA (nvidia-smi), AMD (amdgpu sysfs), Intel (i915/xe sysfs).

The three vendors have different data ceilings. NVIDIA exposes everything
(utilisation, VRAM, temperature, power, per-process usage) via nvidia-smi.
AMD's sysfs interface has no per-process accounting at all. Intel's i915/xe
sysfs exposes no utilisation figure; a real reading needs intel_gpu_top,
which isn't bundled in the container image and needs CAP_PERFMON to run
even when present. Fields that can't be read are reported as None, never a
fabricated 0 (the BUG-33 precedent in system.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from buoy.subprocess_utils import communicate

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

logger = logging.getLogger("buoy.collectors.gpu")

_GPU_CACHE_TTL = 5.0

# Real GPU device dirs only ("card0", "card1", ...) — excludes connector
# subdirectories like "card0-HDMI-A-1" that also live under /sys/class/drm.
_DRM_CARD_RE = re.compile(r"^card\d+$")

_AMD_VENDOR_ID = "0x1002"
_INTEL_VENDOR_ID = "0x8086"

# Overridable so tests can point probing at a fake sysfs tree under tmp_path.
_DRM_BASE = Path("/sys/class/drm")

_NA_TOKENS = {"", "[N/A]", "[Not Supported]", "N/A", "[Unknown Error]"}


async def _run_gpu_tool(*args: str, timeout: float = 5) -> str | None:
    """Run a GPU CLI tool with nsenter (container w/ pid:host) first, then a
    direct call as a fallback for a native (non-container) install — mirrors
    buoy.smartctl.run_smartctl's fallback pattern.
    """
    nsenter_cmd = ["nsenter", "-t", "1", "-m", "--", *args]
    direct_cmd = list(args)

    for cmd in (nsenter_cmd, direct_cmd):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await communicate(proc, timeout=timeout)
            if proc.returncode == 0 and stdout:
                return stdout.decode()
        except (TimeoutError, FileNotFoundError):
            continue
    return None


def _to_number(token: str | None) -> float | int | None:
    """Parse an nvidia-smi CSV token to a number, mapping N/A-ish tokens to None."""
    token = (token or "").strip()
    if token in _NA_TOKENS:
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    return int(value) if value.is_integer() else round(value, 1)


def _to_int(token: str | None) -> int | None:
    try:
        return int((token or "").strip())
    except ValueError:
        return None


# ── NVIDIA backend ─────────────────────────────────────────────────────────

_NVIDIA_QUERY_FIELDS = (
    "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit"
)


async def _probe_nvidia() -> list[dict]:
    output = await _run_gpu_tool(
        "nvidia-smi",
        f"--query-gpu={_NVIDIA_QUERY_FIELDS}",
        "--format=csv,noheader,nounits",
    )
    if not output:
        return []

    gpus = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue
        index, name, util, mem_used, mem_total, temp, power, power_limit = parts[:8]
        gpus.append(
            {
                "vendor": "nvidia",
                "index": _to_int(index),
                "name": name or "NVIDIA GPU",
                "util_pct": _to_number(util),
                "mem_used_mb": _to_number(mem_used),
                "mem_total_mb": _to_number(mem_total),
                "temp": _to_number(temp),
                "power_w": _to_number(power),
                "power_limit_w": _to_number(power_limit),
            }
        )
    return gpus


async def _probe_nvidia_processes() -> list[dict]:
    output = await _run_gpu_tool(
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,used_gpu_memory",
        "--format=csv,noheader,nounits",
    )
    if not output:
        return []

    processes = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        pid, name, mem = parts[:3]
        processes.append({"pid": _to_int(pid), "name": name, "mem_mb": _to_number(mem)})
    return processes


# ── sysfs helpers (shared by AMD + Intel) ───────────────────────────────────


def _iter_drm_cards(base: Path):
    if not base.is_dir():
        return
    for entry in sorted(base.iterdir()):
        if _DRM_CARD_RE.match(entry.name):
            yield entry


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return text or None


def _read_int(path: Path) -> int | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _card_vendor(card: Path) -> str | None:
    return _read_text(card / "device" / "vendor")


def _bytes_to_mb(value: int | None) -> float | None:
    if value is None:
        return None
    return round(value / (1024 * 1024), 1)


# ── AMD backend ──────────────────────────────────────────────────────────────


def _amd_hwmon(device: Path) -> tuple[int | None, float | None, float | None]:
    """Read temp/power from the first hwmon* dir under device/hwmon/.

    The hwmon index isn't stable across boots, so this globs rather than
    hardcoding "hwmon0".
    """
    hwmon_dir = device / "hwmon"
    if not hwmon_dir.is_dir():
        return None, None, None

    for hwmon in sorted(hwmon_dir.iterdir()):
        if not hwmon.name.startswith("hwmon"):
            continue
        temp_raw = _read_int(hwmon / "temp1_input")
        power_raw = _read_int(hwmon / "power1_average")
        power_cap_raw = _read_int(hwmon / "power1_cap")
        temp = temp_raw / 1000 if temp_raw is not None else None
        power = round(power_raw / 1_000_000, 1) if power_raw is not None else None
        power_limit = round(power_cap_raw / 1_000_000, 1) if power_cap_raw is not None else None
        return temp, power, power_limit

    return None, None, None


async def _probe_amd() -> list[dict]:
    gpus = []
    for idx, card in enumerate(
        c for c in _iter_drm_cards(_DRM_BASE) if _card_vendor(c) == _AMD_VENDOR_ID
    ):
        device = card / "device"
        util = _read_int(device / "gpu_busy_percent")
        mem_used = _bytes_to_mb(_read_int(device / "mem_info_vram_used"))
        mem_total = _bytes_to_mb(_read_int(device / "mem_info_vram_total"))
        temp, power, power_limit = _amd_hwmon(device)

        # A card dir whose vendor happens to match but exposes none of the
        # metric files isn't a real amdgpu device we can report on.
        if all(v is None for v in (util, mem_used, mem_total, temp, power)):
            continue

        gpus.append(
            {
                "vendor": "amd",
                "index": idx,
                "name": f"AMD GPU ({card.name})",
                "util_pct": util,
                "mem_used_mb": mem_used,
                "mem_total_mb": mem_total,
                "temp": temp,
                "power_w": power,
                "power_limit_w": power_limit,
            }
        )
    return gpus


# ── Intel backend ────────────────────────────────────────────────────────────


def _parse_intel_gpu_top_busy(output: str) -> float | None:
    """Extract the Render/3D engine busy% from intel_gpu_top -J output.

    Best-effort: intel_gpu_top is a continuous monitor killed mid-write by
    our timeout, so its trailing JSON array is often unterminated — tolerate
    that instead of raising.
    """
    text = output.strip().strip(",")
    if not text.startswith("["):
        text = f"[{text}]"
    try:
        frames = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not frames:
        return None

    engines = frames[-1].get("engines", {}) if isinstance(frames[-1], dict) else {}
    render = engines.get("Render/3D") or engines.get("Render/3D/0")
    if not isinstance(render, dict):
        return None
    try:
        return float(render.get("busy"))
    except (TypeError, ValueError):
        return None


class GpuCollector:
    """Collects GPU utilisation/VRAM/temperature/power across vendors.

    Detection is probed once and cached forever (self._available), mirroring
    DockerCollector.is_available() — a GPU-less host must not spawn a probe
    subprocess on every 5s stats tick. Once at least one GPU is found,
    readings are refreshed on a short TTL, and only the vendors that were
    actually found keep getting re-probed.
    """

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._available: bool | None = None
        self._present_vendors: set[str] = set()
        self._gpus_cache: list[dict] | None = None
        self._gpus_cache_ts: float = 0.0
        self._lock = asyncio.Lock()
        self._intel_gpu_top_available: bool | None = None

    async def collect_summary(self) -> dict:
        """Collect GPU list for the stats endpoint. Omits the key entirely
        (not an empty list) when no GPU is present — same contract as nvme."""
        gpus = await self._read_gpus()
        if not gpus:
            return {}
        return {"gpus": gpus}

    async def collect_detail(self) -> dict:
        gpus = await self._read_gpus()
        processes = await _probe_nvidia_processes() if "nvidia" in self._present_vendors else []
        return {"gpus": gpus, "processes": processes}

    # ── Detection + caching ───────────────────────────────────────────────

    async def _read_gpus(self) -> list[dict]:
        if self._available is False:
            return []

        now = time.monotonic()
        if self._gpus_cache is not None and now - self._gpus_cache_ts < _GPU_CACHE_TTL:
            return self._gpus_cache

        async with self._lock:
            now = time.monotonic()
            if self._gpus_cache is not None and now - self._gpus_cache_ts < _GPU_CACHE_TTL:
                return self._gpus_cache
            if self._available is False:
                return []

            gpus = await self._probe_all()

            if self._available is None:
                self._available = bool(gpus)
                self._present_vendors = {g["vendor"] for g in gpus}
                if gpus:
                    logger.info(
                        "GPU detected: %s",
                        ", ".join(f"{g['vendor']}:{g['name']}" for g in gpus),
                    )

            self._gpus_cache = gpus
            self._gpus_cache_ts = time.monotonic()
            return gpus

    async def _probe_all(self) -> list[dict]:
        # On the very first (detecting) call, try every vendor. After that,
        # only re-probe vendors that actually found something last time.
        first_run = self._available is None
        probe_nvidia = first_run or "nvidia" in self._present_vendors
        probe_amd = first_run or "amd" in self._present_vendors
        probe_intel = first_run or "intel" in self._present_vendors

        results = await asyncio.gather(
            _probe_nvidia() if probe_nvidia else _empty_list(),
            _probe_amd() if probe_amd else _empty_list(),
            self._probe_intel() if probe_intel else _empty_list(),
        )
        return [gpu for vendor_gpus in results for gpu in vendor_gpus]

    # ── Intel (instance method: caches intel_gpu_top availability) ─────────

    async def _probe_intel(self) -> list[dict]:
        cards = [c for c in _iter_drm_cards(_DRM_BASE) if _card_vendor(c) == _INTEL_VENDOR_ID]
        if not cards:
            return []

        util = await self._intel_gpu_top_utilization()

        gpus = []
        for idx, card in enumerate(cards):
            device = card / "device"
            gpus.append(
                {
                    "vendor": "intel",
                    "index": idx,
                    "name": f"Intel GPU ({card.name})",
                    "util_pct": util,
                    "mem_used_mb": None,
                    "mem_total_mb": None,
                    "temp": None,
                    "power_w": None,
                    "power_limit_w": None,
                    "freq_mhz": _read_int(device / "gt_cur_freq_mhz"),
                    "freq_max_mhz": _read_int(device / "gt_max_freq_mhz"),
                    "util_note": None
                    if util is not None
                    else "utilization requires intel_gpu_top (unavailable)",
                }
            )
        return gpus

    async def _intel_gpu_top_utilization(self) -> float | None:
        """Best-effort one-shot utilisation read via `intel_gpu_top -J`.

        intel_gpu_top is neither bundled in the container image nor usable
        without CAP_PERFMON, so this almost always short-circuits on the
        shutil.which() check and util_pct stays None.
        """
        if self._intel_gpu_top_available is False:
            return None
        if not shutil.which("intel_gpu_top"):
            self._intel_gpu_top_available = False
            return None

        output = await _run_gpu_tool("intel_gpu_top", "-J", "-s", "200", timeout=3)
        self._intel_gpu_top_available = bool(output)
        if not output:
            return None
        return _parse_intel_gpu_top_busy(output)


async def _empty_list() -> list[dict]:
    return []
