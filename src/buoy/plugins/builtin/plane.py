"""Plane plugin — sprint/cycle progress from Plane project management."""

from __future__ import annotations

import json
import ssl
import urllib.request
from datetime import date

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class PlanePlugin(Plugin):
    """Shows current sprint/cycle progress from Plane."""

    manifest = PluginManifest(
        id="plane",
        name="Sprint",
        icon="📋",
        description="Current cycle progress",
        version="1.0.0",
        config_schema={
            "api_key": {"type": "string", "required": True},
            "url": {"type": "string", "required": True},
            "workspace": {"type": "string", "required": True},
            "project": {"type": "string", "required": True},
        },
        refresh_interval=120,
    )

    async def collect(self) -> PanelData:
        api_key = self.config.get("api_key", "")
        plane_url = self.config.get("url", "")
        workspace = self.config.get("workspace", "")
        project = self.config.get("project", "")

        if not all([api_key, plane_url, workspace, project]):
            return PanelData(status="disabled", summary="Not configured")

        try:
            url = (
                f"{plane_url.rstrip('/')}/api/v1/workspaces/{workspace}/projects/{project}/cycles/"
            )
            req = urllib.request.Request(
                url, headers={"x-api-key": api_key, "Accept": "application/json"}
            )
            # Allow self-signed certs (common in self-hosted Plane)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_peer = False

            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                cycles = json.loads(resp.read())

            # Find active cycle
            today = date.today().isoformat()
            cycle_list = cycles.get("results", []) if isinstance(cycles, dict) else cycles
            active = None
            for c in cycle_list:
                if c.get("start_date") and c.get("end_date"):
                    if c["start_date"] <= today <= c["end_date"]:
                        active = c
                        break

            if not active:
                return PanelData(status="ok", summary="No active cycle", detail={"cycle": None})

            total = active.get("total_issues", 0)
            completed = active.get("completed_issues", 0)
            pct = round((completed / total) * 100) if total > 0 else 0
            end_date = active.get("end_date", "")
            days_left = (date.fromisoformat(end_date) - date.today()).days if end_date else 0

            return PanelData(
                status="ok",
                summary=f"{pct}% complete ({completed}/{total})",
                detail={
                    "cycle": active.get("name", ""),
                    "start": active.get("start_date", ""),
                    "end": end_date,
                    "total_issues": total,
                    "completed_issues": completed,
                    "pct": pct,
                    "days_left": max(0, days_left),
                },
            )
        except Exception as e:
            return PanelData(status="error", summary="API error", detail={"error": str(e)})

    def frontend_js(self) -> str | None:
        return """
function render_plane(data) {
  if (!data.detail.cycle) return '<div style="font-size:0.6rem;color:var(--text-dim)">No active cycle</div>';
  const d = data.detail;
  return '<div style="padding:0.5rem 0"><div style="font-family:Outfit,sans-serif;font-weight:600;font-size:0.75rem;color:var(--text-bright);margin-bottom:0.4rem">' + d.cycle + '</div><div style="height:6px;background:var(--border);border-radius:3px;overflow:hidden;margin-bottom:0.4rem"><div style="height:100%;width:' + d.pct + '%;background:var(--cyan);border-radius:3px"></div></div><div style="font-size:0.55rem;color:var(--text-dim);display:flex;gap:1rem"><span>' + d.completed_issues + '/' + d.total_issues + ' items</span><span>' + d.pct + '%</span><span>' + d.days_left + 'd left</span></div></div>';
}
"""
