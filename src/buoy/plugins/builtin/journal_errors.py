"""Journal errors plugin — priority-error entries from the host systemd journal."""

from __future__ import annotations

import asyncio
import re

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class JournalErrorsPlugin(Plugin):
    """Shows count and recent priority-error journal entries from the host."""

    manifest = PluginManifest(
        id="journal_errors",
        name="Journal",
        icon="🚨",
        description="Priority-error journal entries",
        version="1.0.0",
        config_schema={
            "since": {"type": "string", "default": "24 hours ago"},
            "max_entries": {"type": "integer", "default": 20},
        },
        refresh_interval=300,
    )

    async def collect(self) -> PanelData:
        entries = await self._read_journal()
        count = len(entries)
        status = "ok" if count == 0 else "warn" if count <= 5 else "error"
        summary = f"{count} error{'s' if count != 1 else ''} (24h)"
        return PanelData(
            status=status,
            summary=summary,
            detail={"entries": entries},
        )

    async def _read_journal(self) -> list[dict]:
        since = self.config.get("since", "24 hours ago")
        max_entries = int(self.config.get("max_entries", 20))
        try:
            proc = await asyncio.create_subprocess_exec(
                "nsenter",
                "-t",
                "1",
                "-m",
                "--",
                "bash",
                "-c",
                f"journalctl --priority=err --since '{since}' --no-pager -q 2>/dev/null"
                f" | tail -{max_entries}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if not stdout:
                return []

            pattern = re.compile(r"^(\w{3}\s+\d+\s+[\d:]+)\s+\S+\s+(\S+?)(?:\[\d+\])?:\s*(.*)$")
            entries = []
            for line in stdout.decode().strip().split("\n"):
                if not line:
                    continue
                match = pattern.match(line)
                if match:
                    entries.append(
                        {
                            "time": match.group(1),
                            "unit": match.group(2),
                            "message": match.group(3)[:200],
                        }
                    )
                else:
                    entries.append({"time": "", "unit": "", "message": line[:200]})
            return entries
        except (TimeoutError, FileNotFoundError):
            return []

    def frontend_js(self) -> str | None:
        return """
function render_journal_errors(data) {
  const entries = data.detail.entries || [];
  if (!entries.length) return '<div style="font-size:0.6rem;color:var(--text-dim)">No journal errors in 24h</div>';
  let html = '<div style="max-height:200px;overflow-y:auto">';
  html += '<table style="width:100%;border-collapse:collapse;font-size:0.5rem">';
  html += '<tr><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Time</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Unit</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Message</th></tr>';
  entries.forEach(e => {
    html += '<tr><td style="padding:0.2rem 0.4rem;color:var(--text-dim);white-space:nowrap">' + e.time.slice(4) + '</td><td style="padding:0.2rem 0.4rem;color:var(--text);white-space:nowrap">' + e.unit + '</td><td style="padding:0.2rem 0.4rem;color:var(--red);max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + e.message + '</td></tr>';
  });
  html += '</table></div>';
  return html;
}
"""
