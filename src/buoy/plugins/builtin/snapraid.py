"""SnapRAID parity status plugin — last sync, unsynced files, disk health."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class SnapraidPlugin(Plugin):
    """Reports SnapRAID parity status: sync age, unsynced count, disk errors."""

    manifest = PluginManifest(
        id="snapraid",
        name="SnapRAID",
        icon="🛡️",
        description="Parity status & disk health",
        version="1.0.0",
        config_schema={
            "status_file": {"type": "string", "default": "/var/snapraid/snapraid.status"},
            "sync_max_age_hours": {"type": "integer", "default": 24},
        },
        refresh_interval=300,
    )

    async def collect(self) -> PanelData:
        text = await self._read_status()
        if text is None:
            return PanelData(status="disabled", summary="Not configured")

        parsed = _parse_status(text)
        max_age = int(self.config.get("sync_max_age_hours", 24))

        if parsed["disk_errors"]:
            status = "error"
            summary = "Disk errors detected"
        elif parsed["unsynced_count"] > 0:
            status = "warn"
            summary = f"{parsed['unsynced_count']:,} unsynced"
        elif parsed["last_sync_age_hours"] is not None and parsed["last_sync_age_hours"] > max_age:
            status = "warn"
            age = parsed["last_sync_age_hours"]
            summary = f"Stale sync ({age:.0f}h ago)"
        else:
            status = "ok"
            age = parsed["last_sync_age_hours"]
            summary = f"synced {age:.0f}h ago" if age is not None else "synced"

        return PanelData(status=status, summary=summary, detail=parsed)

    async def _read_status(self) -> str | None:
        status_file = self.config.get("status_file", "")
        if status_file:
            p = Path(status_file)
            if p.exists():
                return p.read_text(errors="replace")

        # Fallback: run snapraid status via nsenter
        try:
            proc = await asyncio.create_subprocess_exec(
                "nsenter",
                "-t",
                "1",
                "-m",
                "--",
                "snapraid",
                "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode(errors="replace") if stdout else None
        except (TimeoutError, FileNotFoundError):
            return None

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        rows: list[dict] = []
        if d.get("last_sync_age_hours") is not None:
            rows.append(
                {"label": "Last sync", "value": f"{d['last_sync_age_hours']}h ago", "status": None}
            )
        if d.get("unsynced_count") is not None:
            rows.append({"label": "Unsynced", "value": f"{d['unsynced_count']:,}", "status": None})
        if d.get("scrub_pct") is not None:
            rows.append({"label": "Scrubbed", "value": f"{d['scrub_pct']}%", "status": None})
        disk_errors = bool(d.get("disk_errors"))
        rows.append(
            {
                "label": "Disk errors",
                "value": "YES" if disk_errors else "none",
                "status": "error" if disk_errors else None,
            }
        )
        return [panel.keyvalue(rows)]


def _parse_status(text: str) -> dict:
    """Parse snapraid status output into a structured dict."""
    unsynced_count = 0
    last_sync_age_hours: float | None = None
    disk_errors = False
    scrub_pct: int | None = None

    # Unsynced files: "X files to sync" or "X changes"
    m = re.search(r"(\d+)\s+(?:file[s]?|change[s]?)\s+(?:to sync|not synced)", text, re.IGNORECASE)
    if m:
        unsynced_count = int(m.group(1))

    # "Files are not in sync" — try a secondary count pattern if primary missed
    if re.search(r"Files are not in sync", text, re.IGNORECASE) and unsynced_count == 0:
        m2 = re.search(r"(\d+)\s+(?:file|change)", text, re.IGNORECASE)
        if m2:
            unsynced_count = int(m2.group(1))

    # Last sync age: "  5d:12h:34m:56s  Last sync" or "since last sync"
    m = re.search(
        r"(\d+)d:(\d+)h:(\d+)m:\d+s\s+(?:Last sync|since last sync)",
        text,
        re.IGNORECASE,
    )
    if m:
        last_sync_age_hours = int(m.group(1)) * 24 + int(m.group(2)) + int(m.group(3)) / 60
    else:
        m = re.search(r"(\d+)h\s*(\d+)m\s+ago", text, re.IGNORECASE)
        if m:
            last_sync_age_hours = int(m.group(1)) + int(m.group(2)) / 60

    # Scrub percentage
    m = re.search(r"(\d+)%\s+(?:of the array is|scrubbed)", text, re.IGNORECASE)
    if m:
        scrub_pct = int(m.group(1))

    # Disk/scrub errors
    if re.search(r"\bDANGER\b", text):
        disk_errors = True
    elif re.search(r"\b([1-9]\d*)\s+error[s]?\b", text, re.IGNORECASE):
        disk_errors = True

    return {
        "unsynced_count": unsynced_count,
        "last_sync_age_hours": round(last_sync_age_hours, 1)
        if last_sync_age_hours is not None
        else None,
        "disk_errors": disk_errors,
        "scrub_pct": scrub_pct,
    }
