"""Portainer plugin — remote container stats via Portainer agent API."""

from __future__ import annotations

import json
import ssl
import urllib.request

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_MAX_CONTAINERS = 20


class PortainerPlugin(Plugin):
    """Shows container health from a remote Portainer endpoint."""

    manifest = PluginManifest(
        id="portainer",
        name="Portainer",
        icon="🐳",
        description="Remote container stats via Portainer API",
        version="1.0.0",
        config_schema={
            "url": {"type": "string", "required": True},
            "api_key": {"type": "string", "required": True},
            "endpoint_id": {"type": "string", "required": True},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "")
        api_key = self.config.get("api_key", "")
        endpoint_id = self.config.get("endpoint_id", "")
        verify_ssl = self.config.get("verify_ssl", True)

        if not url or not api_key or not endpoint_id:
            return PanelData(status="disabled", summary="Not configured")

        try:
            api_url = f"{url.rstrip('/')}/api/endpoints/{endpoint_id}/docker/containers/json?all=1"
            req = urllib.request.Request(
                api_url,
                headers={"X-API-Key": api_key, "Accept": "application/json"},
            )

            ctx = None
            if not verify_ssl:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                containers = json.loads(resp.read())

            running = 0
            unhealthy = 0
            items = []

            for c in containers:
                name = (c.get("Names") or ["/unknown"])[0].lstrip("/")
                image = c.get("Image", "")
                state = c.get("State", "")
                status_str = c.get("Status", "")

                if "(unhealthy)" in status_str:
                    health = "unhealthy"
                elif "(healthy)" in status_str:
                    health = "healthy"
                elif "(health: starting)" in status_str:
                    health = "starting"
                else:
                    health = "none"

                is_running = state == "running"
                if is_running:
                    running += 1
                if not is_running or health == "unhealthy":
                    unhealthy += 1

                items.append({"name": name, "image": image, "state": state, "health": health})

            total = len(containers)
            status = "warn" if unhealthy > 0 else "ok"
            summary = f"{running}/{total} running"

            return PanelData(
                status=status,
                summary=summary,
                detail={"containers": items[:_MAX_CONTAINERS], "running": running, "total": total},
            )
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})
