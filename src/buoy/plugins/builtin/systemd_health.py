"""Systemd service health plugin — checks configured systemd unit states via nsenter."""

from __future__ import annotations

import asyncio

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class SystemdHealthPlugin(Plugin):
    """Checks configured systemd units via nsenter + systemctl is-active."""

    manifest = PluginManifest(
        id="systemd_health",
        name="Systemd",
        icon="🛠️",
        description="Systemd service health checks",
        version="1.0.0",
        config_schema={
            "units": {"type": "array", "default": []},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        units = self.config.get("units", [])
        if not units:
            return PanelData(status="disabled", summary="Not configured")

        states = await asyncio.gather(*[self._check_unit(u) for u in units])
        unit_rows = [{"unit": u, "state": s} for u, s in zip(units, states)]

        active_count = sum(1 for s in states if s == "active")
        failed = any(s == "failed" for s in states)
        inactive = any(s != "active" for s in states)

        if failed:
            failed_names = [u for u, s in zip(units, states) if s == "failed"]
            summary = f"{failed_names[0]} failed"
            status = "error"
        elif inactive:
            summary = f"{active_count}/{len(units)} active"
            status = "warn"
        else:
            summary = f"{active_count}/{len(units)} active"
            status = "ok"

        return PanelData(
            status=status,
            summary=summary,
            detail={"units": unit_rows},
        )

    async def _check_unit(self, unit: str) -> str:
        # systemctl is-active exits non-zero for non-active units; read stdout regardless
        try:
            proc = await asyncio.create_subprocess_exec(
                "nsenter",
                "-t",
                "1",
                "-m",
                "--",
                "systemctl",
                "is-active",
                unit,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip() if stdout else "unknown"
        except (FileNotFoundError, TimeoutError):
            return "unknown"

    def render(self, data: PanelData) -> list[dict] | None:
        units = data.detail.get("units") or []
        if not units:
            return [panel.text("No units configured", status="dim")]

        state_status = {"active": "ok", "failed": "error"}
        rows = [
            [
                panel.cell(u.get("unit", "")),
                panel.cell(u.get("state", ""), status=state_status.get(u.get("state"), "warn")),
            ]
            for u in units
        ]
        return [panel.table(["Unit", "State"], rows)]
