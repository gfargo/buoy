"""Backup status plugin — checks health of backup files in a directory."""

from __future__ import annotations

import time
from pathlib import Path

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


def _fmt_size(size_bytes: int) -> str:
    if size_bytes > 1048576:
        return f"{size_bytes / 1048576:.1f} MB"
    if size_bytes > 1024:
        return f"{size_bytes // 1024} KB"
    return f"{size_bytes} B"


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
            return PanelData(
                status="error",
                summary="No backups found",
                detail={"path": backup_dir, "pattern": pattern},
            )

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

        size_str = _fmt_size(size_bytes)
        status = "ok" if healthy else "error"
        summary = f"{age_hours:.0f}h ago, {size_str}" if healthy else "; ".join(issues)

        recent_files = []
        for f in files[:20]:
            try:
                f_stat = f.stat()
            except OSError:
                continue
            recent_files.append(
                {
                    "name": f.name,
                    "size": _fmt_size(f_stat.st_size),
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(f_stat.st_mtime)),
                }
            )

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
                "files": recent_files,
            },
        )

    def demo_data(self) -> PanelData:
        files = [
            {
                "name": f"demo-node-2026-08-{23 - i:02d}.sql.gz",
                "size": f"{428.3 - i * 3.1:.1f} MB",
                "mtime": f"2026-08-{23 - i:02d} 03:00",
            }
            for i in range(14)
        ]
        return PanelData(
            status="ok",
            summary="6h ago, 428.3 MB",
            detail={
                "latest_file": files[0]["name"],
                "size": files[0]["size"],
                "age_hours": 6.0,
                "total_count": len(files),
                "healthy": True,
                "issues": [],
                "files": files,
            },
        )

    def render(self, data: PanelData) -> list[dict] | None:
        blocks = self._keyvalue_blocks(data)
        if blocks is None:
            return [panel.text(data.summary or "Unavailable", status="dim")]
        return blocks

    def render_detail(self, data: PanelData) -> list[dict] | None:
        blocks = self._keyvalue_blocks(data)
        if blocks is None:
            return [panel.text(data.summary or "Unavailable", status="dim")]

        files = data.detail.get("files") or []
        if files:
            rows = [
                [
                    panel.cell(f.get("name", ""), mono=True),
                    panel.cell(f.get("size", "")),
                    panel.cell(f.get("mtime", "")),
                ]
                for f in files
            ]
            blocks.append(panel.table(["File", "Size", "Modified"], rows))
        return blocks

    def _keyvalue_blocks(self, data: PanelData) -> list[dict] | None:
        """Shared keyvalue + issues rendering for render() and render_detail().

        Returns None when detail lacks a latest_file (the dir-not-found / no-backups
        early-return paths from collect()), so callers fall back to a text block.
        """
        detail = data.detail
        if "latest_file" not in detail:
            return None

        blocks: list[dict] = [
            panel.keyvalue(
                [
                    ("Latest file", detail.get("latest_file", "")),
                    ("Age", f"{detail.get('age_hours', 0):.1f}h"),
                    ("Size", detail.get("size", "")),
                    ("Count", str(detail.get("total_count", 0))),
                    ("Healthy", "yes" if detail.get("healthy") else "no"),
                ]
            )
        ]
        issues = detail.get("issues") or []
        for issue in issues:
            blocks.append(panel.text(issue, status="error"))
        return blocks
