"""DNS Filter plugin — Pi-hole or AdGuard Home stats."""

from __future__ import annotations

import base64
import json
import urllib.request

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class DnsFilterPlugin(Plugin):
    """Shows DNS filtering stats from Pi-hole or AdGuard Home."""

    manifest = PluginManifest(
        id="dns_filter",
        name="DNS",
        icon="🛡️",
        description="DNS filtering stats (Pi-hole or AdGuard Home)",
        version="1.0.0",
        config_schema={
            "type": {"type": "string", "default": "pihole"},
            "url": {"type": "string", "required": True},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "")
        if not url:
            return PanelData(status="disabled", summary="Not configured")

        dns_type = self.config.get("type", "pihole")
        try:
            if dns_type == "adguard":
                return await self._collect_adguard(url)
            return await self._collect_pihole(url)
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    async def _collect_pihole(self, url: str) -> PanelData:
        api_url = f"{url.rstrip('/')}/admin/api.php?summaryRaw"
        req = urllib.request.Request(api_url)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        queries_today = int(data.get("dns_queries_today", 0))
        blocked_today = int(data.get("ads_blocked_today", 0))
        resolver_status = data.get("status", "enabled")

        # Pi-hole returns percentage as string or float
        try:
            blocked_pct = float(data.get("ads_percentage_today", 0))
        except (TypeError, ValueError):
            blocked_pct = (blocked_today / queries_today * 100) if queries_today else 0.0

        top_blocked: list[dict] = []
        api_key = self.config.get("api_key", "")
        if api_key:
            try:
                top_url = f"{url.rstrip('/')}/admin/api.php?topItems&auth={api_key}"
                with urllib.request.urlopen(urllib.request.Request(top_url), timeout=8) as resp:
                    top_data = json.loads(resp.read())
                top_blocked = [
                    {"domain": d, "count": c}
                    for d, c in (top_data.get("top_ads") or {}).items()
                ]
            except Exception:
                pass  # top_blocked stays empty — non-fatal

        if resolver_status == "disabled":
            return PanelData(
                status="error",
                summary="Filtering disabled",
                detail={"queries_today": queries_today, "blocked_today": blocked_today,
                        "blocked_pct": round(blocked_pct, 1), "top_blocked": top_blocked},
            )

        status = "warn" if blocked_pct > 25 else "ok"
        summary = f"{queries_today:,} queries · {blocked_pct:.1f}% blocked"
        return PanelData(
            status=status,
            summary=summary,
            detail={"queries_today": queries_today, "blocked_today": blocked_today,
                    "blocked_pct": round(blocked_pct, 1), "top_blocked": top_blocked},
        )

    async def _collect_adguard(self, url: str) -> PanelData:
        api_url = f"{url.rstrip('/')}/control/stats"
        req = urllib.request.Request(api_url)

        username = self.config.get("username", "")
        password = self.config.get("password", "")
        if not username and not password:
            # Accept pre-encoded api_key as Basic auth
            api_key = self.config.get("api_key", "")
            if api_key:
                req.add_header("Authorization", f"Basic {api_key}")
        elif username:
            creds = base64.b64encode(f"{username}:{password}".encode()).decode()
            req.add_header("Authorization", f"Basic {creds}")

        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        queries_today = int(data.get("num_dns_queries", 0))
        blocked_today = int(data.get("num_blocked_filtering", 0))
        blocked_pct = (blocked_today / queries_today * 100) if queries_today else 0.0

        # top_blocked_domains: list of {domain: count}
        top_blocked = [
            {"domain": list(entry.keys())[0], "count": list(entry.values())[0]}
            for entry in (data.get("top_blocked_domains") or [])
            if entry
        ]

        status = "warn" if blocked_pct > 25 else "ok"
        summary = f"{queries_today:,} queries · {blocked_pct:.1f}% blocked"
        return PanelData(
            status=status,
            summary=summary,
            detail={"queries_today": queries_today, "blocked_today": blocked_today,
                    "blocked_pct": round(blocked_pct, 1), "top_blocked": top_blocked},
        )

    def frontend_js(self) -> str | None:
        return """
function render_dns_filter(data) {
  const d = data.detail || {};
  const top = d.top_blocked || [];
  let html = '<div style="font-size:0.6rem;margin-bottom:0.5rem">';
  html += '<span style="color:var(--text)">' + (d.queries_today || 0).toLocaleString() + ' queries</span>';
  html += ' &nbsp;·&nbsp; <span style="color:var(--yellow)">' + (d.blocked_pct || 0).toFixed(1) + '% blocked</span>';
  html += '</div>';
  if (top.length) {
    html += '<div style="font-size:0.5rem;color:var(--text-dim);margin-bottom:0.3rem">Top blocked:</div>';
    html += top.slice(0, 5).map(t =>
      '<div style="display:flex;justify-content:space-between;font-size:0.5rem;padding:0.15rem 0;border-bottom:1px solid var(--border)">' +
      '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:80%">' + t.domain + '</span>' +
      '<span style="color:var(--text-dim)">' + t.count + '</span></div>'
    ).join('');
  }
  return html;
}
"""
