"""Speedtest plugin — periodic internet speed tests with trend tracking.

Runs a speed-test binary in a background task (not in collect()) because a speed
test can exceed the 30-second collect() timeout enforced by the loader.

The plugin probes for a usable CLI at startup (in priority order):
  1. ``speedtest`` — official Ookla CLI (recommended; actively maintained)
  2. ``speedtest-cli`` — legacy Python CLI (install via ``pip install buoy[speedtest]``)

If neither binary is found the plugin marks itself unavailable and returns a
descriptive status rather than failing silently or blocking the boot sequence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import statistics
import time
from pathlib import Path

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest
from buoy.subprocess_utils import communicate

logger = logging.getLogger("buoy.plugins.speedtest")

_HISTORY_PATHS = [Path("/data/speedtest_history.json"), Path("speedtest_history.json")]
_MAX_HISTORY = 100

# Candidates tried in order.  The official Ookla CLI is preferred because
# speedtest-cli has been effectively unmaintained since 2021.
_CLI_CANDIDATES = ["speedtest", "speedtest-cli"]


def _find_speedtest_binary() -> str | None:
    """Return the first available speed-test binary, or None."""
    for candidate in _CLI_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    return None


class SpeedtestPlugin(Plugin):
    """Runs internet speed tests on a long interval and tracks download/upload/ping trends."""

    manifest = PluginManifest(
        id="speedtest",
        name="Speedtest",
        icon="🌐",
        description="Periodic internet speed tests with trend tracking",
        version="1.0.0",
        config_schema={
            "interval_hours": {"type": "number", "default": 6},
            "server_id": {"type": "string"},
        },
        refresh_interval=60,
    )

    def __init__(self):
        super().__init__()
        self._history: list[dict] = []
        self._task: asyncio.Task | None = None
        self._binary: str | None = None  # resolved at setup() time

    async def setup(self) -> None:
        self._binary = _find_speedtest_binary()
        self._load_history()
        if self._binary is not None:
            self._task = asyncio.create_task(self._loop())

    async def teardown(self) -> None:
        if self._task:
            self._task.cancel()

    async def collect(self) -> PanelData:
        """Return latest cached result instantly; never blocks on a subprocess."""
        if self._binary is None:
            return PanelData(
                status="unavailable",
                summary="No speedtest binary found",
                detail={
                    "hint": (
                        "Install the official Ookla CLI ('speedtest') or the legacy "
                        "Python wrapper ('pip install buoy[speedtest]') and restart Buoy."
                    )
                },
            )

        if not self._history:
            return PanelData(status="ok", summary="Measuring…", detail={})

        latest = self._history[-1]
        if not latest.get("ok"):
            return PanelData(
                status="error",
                summary="Last test failed",
                detail={"history": self._history[-20:], "error": latest.get("error", "")},
            )

        status = self._compute_status()
        dl = latest.get("download_mbps", 0.0)
        ul = latest.get("upload_mbps", 0.0)
        ping = latest.get("ping_ms", 0.0)
        summary = f"↓ {dl:.0f} Mbps · ↑ {ul:.0f} Mbps · {ping:.0f} ms"

        return PanelData(
            status=status,
            summary=summary,
            detail={
                "latest": latest,
                "history": self._history[-20:],
                "baseline_mbps": self._baseline(),
            },
        )

    def demo_data(self) -> PanelData:
        now = time.time()
        history = [
            {
                "ts": now - (19 - i) * 3600,
                "download_mbps": 480 + (i % 5) * 6,
                "upload_mbps": 90 + (i % 3) * 4,
                "ping_ms": 8 + (i % 4),
                "server": "Demo ISP - Metro",
                "ok": True,
            }
            for i in range(20)
        ]
        latest = history[-1]
        return PanelData(
            status="ok",
            summary=f"↓ {latest['download_mbps']:.0f} Mbps · ↑ {latest['upload_mbps']:.0f} Mbps · {latest['ping_ms']:.0f} ms",
            detail={"latest": latest, "history": history, "baseline_mbps": 480.0},
        )

    # ── Background task ────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        interval_secs = self.config.get("interval_hours", 6) * 3600

        # Run immediately on startup if history is empty or stale
        if not self._history or (time.time() - self._history[-1].get("ts", 0)) > interval_secs:
            await self._run_and_save()

        while True:
            await asyncio.sleep(interval_secs)
            await self._run_and_save()

    async def _run_and_save(self) -> None:
        entry = await self._run_test()
        self._history.append(entry)
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]
        self._save_history()

    async def _run_test(self) -> dict:
        """Run the speed-test binary and return a normalised result dict.

        Two CLIs are supported with different invocation styles and output schemas:

        * ``speedtest`` (Ookla official) — uses ``--format=json --accept-license
          --accept-gdpr``; emits nested objects where ``download.bandwidth`` and
          ``upload.bandwidth`` are in **Bytes/s** and ping is at ``ping.latency``.

        * ``speedtest-cli`` (legacy Python wrapper) — uses ``--json``; emits flat
          keys where ``download`` and ``upload`` are in **bits/s** and ping is at
          ``ping``.
        """
        ts = time.time()
        server_id = self.config.get("server_id")

        binary = self._binary or "speedtest-cli"
        is_ookla = binary == "speedtest"

        if is_ookla:
            cmd = [binary, "--format=json", "--accept-license", "--accept-gdpr"]
            if server_id:
                cmd += ["--server-id", str(server_id)]
        else:
            cmd = [binary, "--json"]
            if server_id:
                cmd += ["--server", str(server_id)]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await communicate(proc, timeout=120)
            data = json.loads(stdout.decode())

            if is_ookla:
                # Ookla CLI: nested schema, bandwidth in Bytes/s (÷ 125_000 → Mbps)
                download_mbps = data["download"]["bandwidth"] / 125_000
                upload_mbps = data["upload"]["bandwidth"] / 125_000
                ping_ms = data["ping"]["latency"]
                server_name = data.get("server", {}).get("name", "")
            else:
                # Legacy speedtest-cli: flat schema, speeds in bits/s (÷ 1e6 → Mbps)
                download_mbps = data["download"] / 1e6
                upload_mbps = data["upload"] / 1e6
                ping_ms = data["ping"]
                server_name = data.get("server", {}).get("name", "")

            return {
                "ts": ts,
                "download_mbps": download_mbps,
                "upload_mbps": upload_mbps,
                "ping_ms": ping_ms,
                "server": server_name,
                "ok": True,
            }
        except FileNotFoundError:
            return {
                "ts": ts,
                "download_mbps": 0.0,
                "upload_mbps": 0.0,
                "ping_ms": 0.0,
                "server": "",
                "ok": False,
                "error": f"{binary} not found",
            }
        except Exception as exc:
            return {
                "ts": ts,
                "download_mbps": 0.0,
                "upload_mbps": 0.0,
                "ping_ms": 0.0,
                "server": "",
                "ok": False,
                "error": str(exc),
            }

    # ── Status & baseline ──────────────────────────────────────────────────────

    def _baseline(self) -> float:
        """Median download (Mbps) over the last 10 successful tests."""
        samples = [e["download_mbps"] for e in self._history if e.get("ok")]
        if not samples:
            return 0.0
        return statistics.median(samples[-10:])

    def _compute_status(self) -> str:
        if not self._history:
            return "ok"
        latest = self._history[-1]
        if not latest.get("ok"):
            return "error"
        baseline = self._baseline()
        if baseline > 0 and latest["download_mbps"] < baseline * 0.5:
            return "warn"
        return "ok"

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_history(self) -> None:
        for path in _HISTORY_PATHS:
            try:
                raw = json.loads(path.read_text())
                if isinstance(raw, list):
                    self._history = raw[-_MAX_HISTORY:]
                    return
            except Exception:
                logger.debug("speedtest: failed to load history from %s", path, exc_info=True)

    def _save_history(self) -> None:
        for path in _HISTORY_PATHS:
            try:
                path.write_text(json.dumps(self._history))
                return
            except Exception:
                logger.debug("speedtest: failed to save history to %s", path, exc_info=True)

    # ── Frontend ───────────────────────────────────────────────────────────────

    def render(self, data: PanelData) -> list[dict] | None:
        detail = data.detail or {}
        latest = detail.get("latest") or {}
        history = detail.get("history") or []
        baseline = detail.get("baseline_mbps") or 0

        if not latest or not latest.get("ok"):
            msg = latest.get("error") if latest else None
            return [panel.text(msg or "Measuring…", status="dim")]

        blocks: list[dict] = []
        dl_points = [e.get("download_mbps", 0) for e in history if e.get("ok")]
        if len(dl_points) > 1:
            spark_status = "warn" if data.status == "warn" else "info"
            blocks.append(panel.sparkline(dl_points, status=spark_status))

        rows = [
            ("↓ Download", f"{latest.get('download_mbps', 0):.0f} Mbps"),
            ("↑ Upload", f"{latest.get('upload_mbps', 0):.0f} Mbps"),
            ("⏱ Ping", f"{latest.get('ping_ms', 0):.0f} ms"),
        ]
        if latest.get("server"):
            rows.append(("Server", latest["server"]))
        if baseline > 0:
            rows.append(("Baseline", f"{baseline:.0f} Mbps"))
        blocks.append(panel.keyvalue(rows))
        return blocks
