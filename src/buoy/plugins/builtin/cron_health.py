"""Cron health plugin — recent cron job runs from journald."""

from __future__ import annotations

import asyncio
import re

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


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
                "nsenter", "-t", "1", "-m", "-n", "--", "bash", "-c",
                "journalctl -u cron --since '24 hours ago' --no-pager -q 2>/dev/null"
                " | grep ') CMD ' | tail -20",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if not stdout:
                return []

            entries = []
            pattern = re.compile(
                r"(\w+ \d+ [\d:]+)\s+\S+\s+CRON\[\d+\]:\s*\((\w+)\)\s+CMD\s+\((.+)\)"
            )
            for line in stdout.decode().strip().split("\n"):
                match = pattern.match(line)
                if match:
                    entries.append({
                        "time": match.group(1),
                        "user": match.group(2),
                        "cmd": match.group(3)[:80],
                    })
            return entries
        except (TimeoutError, FileNotFoundError):
            return []

    def frontend_js(self) -> str | None:
        return """
function render_cron_health(data) {
  const entries = data.detail.entries || [];
  if (!entries.length) return '<div style="font-size:0.6rem;color:var(--text-dim)">No cron activity in 24h</div>';
  let html = '<table style="width:100%;border-collapse:collapse;font-size:0.5rem">';
  html += '<tr><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Time</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">User</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Command</th></tr>';
  entries.slice(0, 10).forEach(e => {
    html += '<tr><td style="padding:0.2rem 0.4rem;color:var(--text);white-space:nowrap">' + e.time.slice(4) + '</td><td style="padding:0.2rem 0.4rem;color:var(--text)">' + e.user + '</td><td style="padding:0.2rem 0.4rem;color:var(--text-dim);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + e.cmd + '</td></tr>';
  });
  html += '</table>';
  return html;
}
"""
