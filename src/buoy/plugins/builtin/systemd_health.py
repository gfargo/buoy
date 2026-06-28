"""Systemd service health plugin — checks configured systemd unit states via nsenter."""

from __future__ import annotations

import asyncio

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

    def frontend_js(self) -> str | None:
        return """
function render_systemd_health(data) {
  const units = data.detail.units || [];
  if (!units.length) return '<div style="font-size:0.6rem;color:var(--text-dim)">No units configured</div>';
  const stateColor = s => s === 'active' ? 'var(--ok)' : s === 'failed' ? 'var(--error)' : 'var(--warn)';
  let html = '<table style="width:100%;border-collapse:collapse;font-size:0.5rem">';
  html += '<tr><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Unit</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">State</th></tr>';
  units.forEach(u => {
    html += '<tr><td style="padding:0.2rem 0.4rem;color:var(--text);white-space:nowrap">' + u.unit + '</td><td style="padding:0.2rem 0.4rem;color:' + stateColor(u.state) + '">' + u.state + '</td></tr>';
  });
  html += '</table>';
  return html;
}
"""
