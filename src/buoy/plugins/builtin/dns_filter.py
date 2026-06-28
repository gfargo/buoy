"""DNS Filter plugin — Pi-hole or AdGuard Home DNS filtering stats."""

from __future__ import annotations

import base64
import json
import ssl
import urllib.request

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_WARN_PCT = 25.0


class DnsFilterPlugin(Plugin):
    """Shows DNS filtering stats from Pi-hole (v5) or AdGuard Home."""

    manifest = PluginManifest(
        id="dns_filter",
        name="DNS Filter",
        icon="🛡️",
        description="Pi-hole / AdGuard Home DNS filtering stats",
        version="1.0.0",
        config_schema={
            "type": {"type": "string", "required": True},  # pihole | adguard
            "url": {"type": "string", "required": True},
            "api_key": {"type": "string"},  # Pi-hole: topItems token; AdGuard: base64 user:pass
            "username": {"type": "string"},  # AdGuard Basic-auth username
            "password": {"type": "string"},  # AdGuard Basic-auth password
            "verify_ssl": {"type": "boolean"},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        dns_type = self.config.get("type", "")
        url = self.config.get("url", "").rstrip("/")
        if not dns_type or not url:
            return PanelData(status="disabled", summary="Not configured")

        verify_ssl = self.config.get("verify_ssl", True)
        ctx = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            if dns_type == "pihole":
                return await self._collect_pihole(url, ctx)
            elif dns_type == "adguard":
                return await self._collect_adguard(url, ctx)
            else:
                return PanelData(status="error", summary=f"Unknown type: {dns_type!r}")
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    async def _collect_pihole(self, url: str, ctx) -> PanelData:
        req = urllib.request.Request(
            f"{url}/admin/api.php?summaryRaw",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read())

        if data.get("status") == "disabled":
            return PanelData(status="error", summary="Filtering disabled")

        queries = int(data.get("dns_queries_today", 0))
        blocked = int(data.get("ads_blocked_today", 0))
        pct = float(data.get("ads_percentage_today", 0.0))

        # Optionally fetch top blocked domains if api_key is provided
        top_blocked: list[dict] = []
        api_key = self.config.get("api_key", "")
        if api_key:
            try:
                top_req = urllib.request.Request(
                    f"{url}/admin/api.php?topItems&auth={api_key}",
                    headers={"Accept": "application/json"},
                )
                with urllib.request.urlopen(top_req, timeout=8, context=ctx) as resp:
                    top_data = json.loads(resp.read())
                top_blocked = [
                    {"domain": d, "count": c} for d, c in (top_data.get("top_ads") or {}).items()
                ]
            except Exception:
                pass  # top domains are best-effort

        return self._make_panel(queries, blocked, pct, top_blocked)

    async def _collect_adguard(self, url: str, ctx) -> PanelData:
        headers: dict[str, str] = {"Accept": "application/json"}

        # Build Basic auth header
        api_key = self.config.get("api_key", "")
        username = self.config.get("username", "")
        password = self.config.get("password", "")
        if api_key:
            # Accept pre-encoded "user:pass" string in api_key
            creds = base64.b64encode(api_key.encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        elif username:
            creds = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"

        req = urllib.request.Request(f"{url}/control/stats", headers=headers)
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read())

        queries = int(data.get("num_dns_queries", 0))
        blocked = int(data.get("num_blocked_filtering", 0))
        pct = (blocked / queries * 100) if queries > 0 else 0.0
        top_blocked = [
            {"domain": d, "count": c} for d, c in (data.get("top_blocked_domains") or {}).items()
        ]

        return self._make_panel(queries, blocked, pct, top_blocked)

    def _make_panel(
        self, queries: int, blocked: int, pct: float, top_blocked: list[dict]
    ) -> PanelData:
        status = "warn" if pct > _WARN_PCT else "ok"
        summary = f"{queries:,} queries · {pct:.1f}% blocked"
        return PanelData(
            status=status,
            summary=summary,
            detail={
                "queries": queries,
                "blocked": blocked,
                "pct": round(pct, 1),
                "top_blocked": top_blocked,
            },
        )

    def frontend_js(self) -> str | None:
        return """
function render_dns_filter(data) {
  const d = data.detail || {};
  const top = d.top_blocked || [];
  let html = '<div style="font-size:0.6rem;margin-bottom:0.4rem"><span style="color:var(--cyan)">' + (d.queries || 0).toLocaleString() + '</span> queries &nbsp;·&nbsp; <span style="color:var(--amber)">' + (d.pct || 0) + '%</span> blocked (' + (d.blocked || 0).toLocaleString() + ')</div>';
  if (top.length) {
    html += '<div style="font-size:0.55rem;color:var(--text-dim);margin-bottom:0.2rem">Top blocked domains</div>';
    html += top.slice(0, 5).map(function(t) {
      return '<div style="display:flex;justify-content:space-between;font-size:0.5rem;padding:0.15rem 0;border-bottom:1px solid var(--border)"><span>' + t.domain + '</span><span style="color:var(--text-dim)">' + t.count + '</span></div>';
    }).join('');
  }
  return html;
}
"""
