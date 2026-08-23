"""Plane plugin — sprint/cycle progress from Plane project management."""

from __future__ import annotations

import json
import ssl
import urllib.request
from datetime import date

from buoy.plugins import panel
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

    def demo_data(self) -> PanelData:
        return PanelData(
            status="ok",
            summary="68% complete (17/25)",
            detail={
                "cycle": "Sprint 42",
                "start": "2026-08-17",
                "end": "2026-08-31",
                "total_issues": 25,
                "completed_issues": 17,
                "pct": 68,
                "days_left": 8,
            },
        )

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        if not d.get("cycle"):
            return [panel.text("No active cycle", status="dim")]

        label = f"{d.get('completed_issues', 0)}/{d.get('total_issues', 0)} items · {d.get('pct', 0)}% · {d.get('days_left', 0)}d left"
        return [panel.text(d["cycle"]), panel.bar(d.get("pct", 0), label=label, status="info")]
