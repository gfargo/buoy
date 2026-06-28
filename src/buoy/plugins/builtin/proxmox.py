"""Proxmox VE plugin — node status, VM and CT guest states."""

from __future__ import annotations

import json
import ssl
import urllib.request

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class ProxmoxPlugin(Plugin):
    """Shows Proxmox VE node status plus QEMU VM and LXC container states."""

    manifest = PluginManifest(
        id="proxmox",
        name="Proxmox",
        icon="🖥️",
        description="Proxmox VE node + guest status",
        version="1.0.0",
        config_schema={
            "url": {"type": "string", "required": True},
            "token_id": {"type": "string", "required": True},
            "token_secret": {"type": "string", "required": True},
            "node": {"type": "string", "required": True},
            "verify_ssl": {"type": "bool", "required": False},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "")
        token_id = self.config.get("token_id", "")
        token_secret = self.config.get("token_secret", "")
        node = self.config.get("node", "")

        if not all([url, token_id, token_secret, node]):
            return PanelData(status="disabled", summary="Not configured")

        base = url.rstrip("/")
        headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
        verify_ssl = self.config.get("verify_ssl", False)
        ctx = None if verify_ssl else ssl._create_unverified_context()

        def get(path: str):
            req = urllib.request.Request(f"{base}{path}", headers=headers)
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                return json.loads(resp.read())["data"]

        try:
            node_status = get(f"/api2/json/nodes/{node}/status")
            vms = get(f"/api2/json/nodes/{node}/qemu")
            cts = get(f"/api2/json/nodes/{node}/lxc")
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

        guests = vms + cts
        stopped = [g for g in guests if g.get("status") != "running"]
        overall = "warn" if stopped else "ok"

        cpu_pct = round(node_status.get("cpu", 0) * 100)
        mem_used = node_status.get("memory", {}).get("used", 0)
        mem_total = node_status.get("memory", {}).get("total", 1)
        mem_pct = round(mem_used / mem_total * 100)

        vm_run = sum(1 for v in vms if v.get("status") == "running")
        ct_run = sum(1 for c in cts if c.get("status") == "running")
        summary = f"{vm_run}/{len(vms)} VMs · {ct_run}/{len(cts)} CTs · CPU {cpu_pct}%"

        return PanelData(
            status=overall,
            summary=summary,
            detail={
                "vms": [{"name": v.get("name"), "status": v.get("status"), "vmid": v.get("vmid")} for v in vms],
                "cts": [{"name": c.get("name"), "status": c.get("status"), "vmid": c.get("vmid")} for c in cts],
                "node": {"cpu_pct": cpu_pct, "mem_pct": mem_pct, "uptime": node_status.get("uptime", 0)},
            },
        )

    def frontend_js(self) -> str | None:
        return """
function render_proxmox(data) {
  const d = data.detail;
  const node = d.node || {};
  let html = '<div style="font-size:0.55rem;color:var(--text-dim);margin-bottom:0.4rem">CPU ' + (node.cpu_pct || 0) + '% · MEM ' + (node.mem_pct || 0) + '%</div>';
  html += '<div style="display:flex;flex-wrap:wrap;gap:0.3rem">';
  const guests = (d.vms || []).concat(d.cts || []);
  guests.forEach(g => {
    const up = g.status === 'running';
    const color = up ? 'var(--green)' : 'var(--red)';
    html += '<div style="display:inline-flex;align-items:center;gap:0.25rem;font-size:0.5rem;padding:0.15rem 0.4rem;border:1px solid var(--border);border-radius:3px"><div style="width:5px;height:5px;border-radius:50%;background:' + color + '"></div>' + g.name + '</div>';
  });
  html += '</div>';
  return html;
}
"""
