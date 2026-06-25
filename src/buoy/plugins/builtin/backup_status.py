"""Backup status plugin — checks health of backup files in a directory."""

from __future__ import annotations

import time
from pathlib import Path

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class BackupStatusPlugin(Plugin):
    """Checks backup directory for recent, valid backup files."""

    manifest = PluginManifest(
        id="backup_status",
        name="Backups",
        icon="💾",
        description="Backup health & freshness",
        version="1.0.0",
        config_schema={
            "backup_dir": {"type": "string", "default": "/backup"},
            "pattern": {"type": "string", "default": "*.sql.gz"},
            "max_age_hours": {"type": "integer", "default": 36},
            "min_size_bytes": {"type": "integer", "default": 100},
        },
        refresh_interval=300,
    )

    async def collect(self) -> PanelData:
        backup_dir = self.config.get("backup_dir", "/backup")
        pattern = self.config.get("pattern", "*.sql.gz")
        max_age_hours = int(self.config.get("max_age_hours", 36))
        min_size = int(self.config.get("min_size_bytes", 100))

        backup_path = Path(backup_dir)
        if not backup_path.exists():
            return PanelData(status="warn", summary="Dir not found", detail={"path": backup_dir})

        # Find backup files matching pattern
        files = sorted(backup_path.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            return PanelData(status="error", summary="No backups found", detail={"path": backup_dir, "pattern": pattern})

        latest = files[0]
        stat = latest.stat()
        age_hours = (time.time() - stat.st_mtime) / 3600
        size_bytes = stat.st_size

        # Determine health
        healthy = True
        issues = []
        if size_bytes < min_size:
            healthy = False
            issues.append(f"too small ({size_bytes}B)")
        if age_hours > max_age_hours:
            healthy = False
            issues.append(f"too old ({age_hours:.0f}h)")

        # Format size
        if size_bytes > 1048576:
            size_str = f"{size_bytes / 1048576:.1f} MB"
        elif size_bytes > 1024:
            size_str = f"{size_bytes // 1024} KB"
        else:
            size_str = f"{size_bytes} B"

        status = "ok" if healthy else "error"
        summary = f"{age_hours:.0f}h ago, {size_str}" if healthy else "; ".join(issues)

        return PanelData(
            status=status,
            summary=summary,
            detail={
                "latest_file": latest.name,
                "size": size_str,
                "age_hours": round(age_hours, 1),
                "total_count": len(files),
                "healthy": healthy,
                "issues": issues,
            },
        )
