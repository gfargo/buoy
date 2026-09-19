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
        config_schema={
            "max_entries": {"type": "integer", "default": 20},
            "backup_log_path": {"type": "string", "default": ""},
        },
        refresh_interval=120,
    )

    async def collect(self) -> PanelData:
        entries = await self._read_cron_log()
        count = len(entries)
        summary = f"{count} job{'s' if count != 1 else ''} (24h)" if count else "No cron activity"
        detail: dict = {"entries": entries}
        backup_log = await self._read_backup_log()
        if backup_log:
            detail["backup_log"] = backup_log
        return PanelData(
            status="ok",
            summary=summary,
            detail=detail,
        )

    async def _read_cron_log(self) -> list[dict]:
        """Read cron CMD entries from journald (last 24h)."""
        max_entries = int(self.config.get("max_entries", 20))
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
                f" | grep ') CMD ' | tail -{max_entries}",
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
                            "cmd": match.group(3),
                        }
                    )
            return entries
        except (TimeoutError, FileNotFoundError):
            return []

    async def _read_backup_log(self) -> list[str]:
        """Tail the configured backup log from the host, if any. Off by default."""
        backup_log_path = self.config.get("backup_log_path", "")
        if not backup_log_path:
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                "nsenter",
                "-t",
                "1",
                "-m",
                "--",
                "tail",
                "-n",
                "50",
                backup_log_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await communicate(proc, timeout=3)
            if not stdout:
                return []
            return stdout.decode(errors="replace").splitlines()
        except (TimeoutError, FileNotFoundError):
            return []

    def demo_data(self) -> PanelData:
        entries = [
            {"time": "Aug 23 00:00:01", "user": "root", "cmd": "/usr/local/bin/backup.sh"},
            {"time": "Aug 23 01:00:01", "user": "root", "cmd": "logrotate /etc/logrotate.conf"},
            {"time": "Aug 23 02:00:01", "user": "root", "cmd": "certbot renew --quiet"},
            {"time": "Aug 23 03:00:01", "user": "buoy", "cmd": "docker image prune -f"},
            {
                "time": "Aug 23 04:00:01",
                "user": "root",
                "cmd": (
                    "rsync -az --delete /srv/media/ backup-nas:/volume1/media-mirror/ "
                    "--exclude '*.tmp' --log-file=/var/log/rsync-media.log"
                ),
            },
            {"time": "Aug 23 05:00:01", "user": "gfargo", "cmd": "python3 ~/scripts/rgb_status.py"},
            {"time": "Aug 23 06:00:01", "user": "root", "cmd": "apt-get -qq update"},
            {"time": "Aug 23 09:00:01", "user": "root", "cmd": "/usr/local/bin/backup.sh"},
            {"time": "Aug 23 12:00:01", "user": "buoy", "cmd": "docker system prune -f --volumes"},
            {"time": "Aug 23 15:00:01", "user": "root", "cmd": "certbot renew --quiet"},
            {"time": "Aug 23 18:00:01", "user": "root", "cmd": "/usr/local/bin/backup.sh"},
            {"time": "Aug 23 21:00:01", "user": "gfargo", "cmd": "python3 ~/scripts/rgb_status.py"},
        ]
        backup_log = [
            "2026-08-23 00:00:01 starting backup of plane, grafana, actual-budget",
            "2026-08-23 00:00:02 dumping plane database...",
            "2026-08-23 00:00:14 dumping grafana database...",
            "2026-08-23 00:00:19 dumping actual-budget data dir...",
            "2026-08-23 00:00:41 uploading archives to backup-nas...",
            "2026-08-23 00:01:03 backup complete: 428.3 MB in 62s",
        ]
        return PanelData(
            status="ok",
            summary=f"{len(entries)} jobs (24h)",
            detail={"entries": entries, "backup_log": backup_log},
        )

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

    def render_detail(self, data: PanelData) -> list[dict] | None:
        entries = data.detail.get("entries") or []
        blocks: list[dict] = []
        if not entries:
            blocks.append(panel.text("No cron activity in 24h", status="dim"))
        else:
            rows = [
                [
                    panel.cell(e.get("time", "")[4:]),
                    panel.cell(e.get("user", "")),
                    panel.cell(e.get("cmd", ""), status="dim", wrap=True),
                ]
                for e in entries
            ]
            blocks.append(panel.table(["Time", "User", "Command"], rows))

        backup_log = data.detail.get("backup_log") or []
        if backup_log:
            blocks.append(panel.heading("Backup log"))
            blocks.append(panel.log(backup_log))

        return blocks
