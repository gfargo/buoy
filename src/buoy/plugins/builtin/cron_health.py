"""Cron health plugin — recent cron job runs from journald."""

from __future__ import annotations

import asyncio
import re

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest
from buoy.subprocess_utils import communicate


class CronHealthPlugin(Plugin):
    """Shows recent cron job executions from system journal."""

    manifest = PluginManifest(
        id="cron_health",
        name="Cron",
        icon="⏰",
        description="Recent cron job runs",
        version="1.0.0",
        config_schema={},
        refresh_interval=120,
    )

    async def collect(self) -> PanelData:
        entries = await self._read_cron_log()
        count = len(entries)
        summary = f"{count} job{'s' if count != 1 else ''} (24h)" if count else "No cron activity"
        return PanelData(
            status="ok",
            summary=summary,
            detail={"entries": entries},
        )

    async def _read_cron_log(self) -> list[dict]:
        """Read cron CMD entries from journald (last 24h)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "nsenter",
                "-t",
                "1",
                "-m",
                "-n",
                "--",
                "bash",
                "-c",
                "journalctl -u cron --since '24 hours ago' --no-pager -q 2>/dev/null"
                " | grep ') CMD ' | tail -20",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await communicate(proc, timeout=5)
            if not stdout:
                return []

            entries = []
            pattern = re.compile(
                r"(\w+ \d+ [\d:]+)\s+\S+\s+CRON\[\d+\]:\s*\((\w+)\)\s+CMD\s+\((.+)\)"
            )
            for line in stdout.decode().strip().split("\n"):
                match = pattern.match(line)
                if match:
                    entries.append(
                        {
                            "time": match.group(1),
                            "user": match.group(2),
                            "cmd": match.group(3)[:80],
                        }
                    )
            return entries
        except (TimeoutError, FileNotFoundError):
            return []

    def demo_data(self) -> PanelData:
        entries = [
            {"time": "Aug 23 03:00:01", "user": "root", "cmd": "/usr/local/bin/backup.sh"},
            {"time": "Aug 23 04:00:01", "user": "root", "cmd": "certbot renew --quiet"},
            {"time": "Aug 23 06:00:01", "user": "buoy", "cmd": "docker image prune -f"},
        ]
        return PanelData(status="ok", summary="3 jobs (24h)", detail={"entries": entries})

    def render(self, data: PanelData) -> list[dict] | None:
        entries = data.detail.get("entries") or []
        if not entries:
            return [panel.text("No cron activity in 24h", status="dim")]

        rows = [
            [
                panel.cell(e.get("time", "")[4:]),
                panel.cell(e.get("user", "")),
                panel.cell(e.get("cmd", ""), status="dim", truncate=True),
            ]
            for e in entries[:10]
        ]
        return [panel.table(["Time", "User", "Command"], rows)]
