"""UptimeKuma plugin — service health badges from status page API."""

from __future__ import annotations

import json
import urllib.request

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class UptimeKumaPlugin(Plugin):
    """Shows service health badges from UptimeKuma."""

    manifest = PluginManifest(
        id="uptime_kuma",
        name="Uptime",
        icon="🟢",
        description="Service health monitoring",
        version="1.0.0",
        config_schema={"url": {"type": "string", "required": True}},
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "")
        if not url:
            return PanelData(status="disabled", summary="Not configured")

        try:
            api_url = f"{url.rstrip('/')}/api/status-page/heartbeat/default"
            req = urllib.request.Request(api_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())

            heartbeats = data.get("heartbeatList", {})
            monitors = []
            up_count = 0
            down_count = 0

            for monitor_id, beats in heartbeats.items():
                if not beats:
                    continue
                latest = beats[-1]
                is_up = latest.get("status") == 1
                name = latest.get("msg", f"Monitor {monitor_id}")
                monitors.append({"name": name, "up": is_up})
                if is_up:
                    up_count += 1
                else:
                    down_count += 1

            total = up_count + down_count
            status = "ok" if down_count == 0 else "error"
            summary = f"{up_count}/{total} up" if total else "No monitors"

            return PanelData(status=status, summary=summary, detail={"monitors": monitors})
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    def frontend_js(self) -> str | None:
        return """
function render_uptime_kuma(data) {
  const monitors = data.detail.monitors || [];
  if (!monitors.length) return '<div style="font-size:0.6rem;color:var(--text-dim)">No monitors</div>';
  return '<div style="display:flex;flex-wrap:wrap;gap:0.4rem">' + monitors.map(m => {
    const color = m.up ? 'var(--green)' : 'var(--red)';
    return '<div style="display:inline-flex;align-items:center;gap:0.3rem;font-size:0.55rem;padding:0.2rem 0.5rem;border:1px solid var(--border);border-radius:3px"><div style="width:6px;height:6px;border-radius:50%;background:' + color + '"></div>' + m.name + '</div>';
  }).join('') + '</div>';
}
"""
