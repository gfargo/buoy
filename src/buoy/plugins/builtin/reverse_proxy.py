"""Reverse proxy plugin — Traefik, Caddy, or Nginx Proxy Manager status.

Shows router/route count, per-host TLS/cert status, and (when a Prometheus
metrics endpoint is configured) 5xx error rate. Backend capabilities differ:

* Traefik  — router/service counts + per-router status from the API;
  per-host cert *expiry* is not available without scraping Prometheus
  metrics, so certs are reported as present/absent only.
* Caddy    — route counts per server + best-effort upstream health from
  ``/reverse_proxy/upstreams``; no cert expiry via the admin API.
* NPM      — the only backend with real per-host certificate expiry
  (Nginx Proxy Manager stores it alongside each proxy host).

Missing signals are omitted rather than treated as errors — a plugin that
can't answer "5xx rate" for Caddy without metrics enabled shouldn't report
that as broken.
"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_MAX_ROWS = 10
_NPM_TOKEN_TTL = 1800  # seconds; re-auth well within NPM's own token lifetime

_METRIC_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(\{(?P<labels>[^}]*)\})?\s+(?P<value>[\d.eE+\-]+)\s*$"
)
_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def _parse_5xx_counters(text: str) -> tuple[float, float]:
    """Sum `*_requests_total` counter samples into (five_xx_total, all_total).

    Only samples carrying a `code`/`status` label are counted at all — some
    exporters (e.g. Caddy's `caddy_http_requests_total`) expose a request
    counter with no response-code label, putting codes on a separate metric
    instead. Including those in the denominator would silently dilute the
    rate toward 0%, so they're excluded entirely rather than guessed at.
    """
    five_xx = 0.0
    total = 0.0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _METRIC_LINE_RE.match(line)
        if not m or not m.group("name").endswith("_requests_total"):
            continue
        labels = dict(_LABEL_RE.findall(m.group("labels") or ""))
        code = labels.get("code") or labels.get("status") or ""
        if not code:
            continue
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        total += value
        if code.startswith("5"):
            five_xx += value
    return five_xx, total


def _npm_days_remaining(expires_on: str, now: float) -> int | None:
    """Return whole days until an NPM `expires_on` ISO timestamp, or None."""
    try:
        dt = datetime.fromisoformat(expires_on.replace("Z", "+00:00"))
        return int((dt.timestamp() - now) / 86400)
    except (ValueError, TypeError):
        return None


class ReverseProxyPlugin(Plugin):
    """Shows router/route count, cert status, and 5xx rate for a reverse proxy."""

    manifest = PluginManifest(
        id="reverse_proxy",
        name="Reverse Proxy",
        icon="🔀",
        description="Traefik / Caddy / Nginx Proxy Manager router status & cert health",
        version="1.0.0",
        config_schema={
            "type": {"type": "string", "required": True},  # traefik | caddy | npm
            "url": {"type": "string", "required": True},
            "api_key": {"type": "string"},  # optional bearer token for an authed admin API
            "username": {"type": "string"},  # NPM login identity
            "password": {"type": "string"},  # NPM login secret
            "metrics_url": {"type": "string"},  # optional Prometheus /metrics endpoint
            "verify_ssl": {"type": "boolean", "default": True},
            "warn_days": {"type": "integer", "default": 30},
            "critical_days": {"type": "integer", "default": 7},
            "error_rate_warn": {"type": "number", "default": 5.0},
        },
        refresh_interval=60,
    )

    def __init__(self) -> None:
        super().__init__()
        self._metrics_prev: tuple[float, float] | None = None
        self._npm_token: str | None = None
        self._npm_token_expiry: float = 0.0

    async def collect(self) -> PanelData:
        rp_type = self.config.get("type", "")
        url = self.config.get("url", "").rstrip("/")
        if not rp_type or not url:
            return PanelData(status="disabled", summary="Not configured")

        verify_ssl = self.config.get("verify_ssl", True)
        ctx = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        headers: dict[str, str] = {}
        api_key = self.config.get("api_key", "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        loop = asyncio.get_running_loop()
        try:
            if rp_type == "traefik":
                return await loop.run_in_executor(None, self._collect_traefik, url, ctx, headers)
            elif rp_type == "caddy":
                return await loop.run_in_executor(None, self._collect_caddy, url, ctx, headers)
            elif rp_type == "npm":
                return await loop.run_in_executor(None, self._collect_npm, url, ctx, headers)
            else:
                return PanelData(status="error", summary=f"Unknown type: {rp_type!r}")
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    # ------------------------------------------------------------------
    # Traefik
    # ------------------------------------------------------------------

    def _collect_traefik(self, url: str, ctx, headers: dict[str, str]) -> PanelData:
        overview = self._get_json(f"{url}/api/overview", ctx, headers)
        routers = self._get_json(f"{url}/api/http/routers", ctx, headers)

        router_summary = (overview.get("http") or {}).get("routers") or {}
        router_count = int(router_summary.get("total", len(routers)))
        overview_errors = int(router_summary.get("errors", 0))
        overview_warnings = int(router_summary.get("warnings", 0))

        hosts = []
        for r in routers:
            r_status = r.get("status", "enabled")
            status = "warn" if r_status in ("disabled", "warning") else "ok"
            has_tls = bool(r.get("tls"))
            hosts.append(
                {
                    "name": r.get("name", "unknown"),
                    "status": status,
                    "detail": r.get("rule", ""),
                    "cert_status": "ok" if has_tls else None,
                    "cert_label": "TLS" if has_tls else "no TLS",
                }
            )

        error_rate = self._maybe_scrape_5xx_rate(ctx, headers)
        data = self._make_panel("traefik", hosts, error_rate, router_count=router_count)
        if overview_errors > 0:
            data.status = "error"
        elif overview_warnings > 0 and data.status == "ok":
            data.status = "warn"
        return data

    # ------------------------------------------------------------------
    # Caddy
    # ------------------------------------------------------------------

    def _collect_caddy(self, url: str, ctx, headers: dict[str, str]) -> PanelData:
        servers = self._get_json(f"{url}/config/apps/http/servers", ctx, headers) or {}

        upstream_fails = 0
        try:
            upstreams = self._get_json(f"{url}/reverse_proxy/upstreams", ctx, headers) or []
            upstream_fails = sum(
                1 for u in upstreams if isinstance(u, dict) and u.get("fails", 0) > 0
            )
        except Exception:
            pass  # best-effort; not every Caddy build/config exposes this endpoint

        hosts = []
        router_count = 0
        for server_name, server in servers.items():
            routes = server.get("routes") or []
            router_count += len(routes)
            auto_https = server.get("automatic_https") or {}
            tls_enabled = not auto_https.get("disable", False)
            hosts.append(
                {
                    "name": server_name,
                    "status": "ok",
                    "detail": f"{len(routes)} route{'s' if len(routes) != 1 else ''}",
                    "cert_status": "ok" if tls_enabled else None,
                    "cert_label": "auto TLS" if tls_enabled else "no TLS",
                }
            )

        error_rate = self._maybe_scrape_5xx_rate(ctx, headers)
        data = self._make_panel("caddy", hosts, error_rate, router_count=router_count)
        if upstream_fails and data.status == "ok":
            data.status = "warn"
            plural = "s" if upstream_fails != 1 else ""
            data.summary += f" · {upstream_fails} upstream{plural} failing"
        return data

    # ------------------------------------------------------------------
    # Nginx Proxy Manager
    # ------------------------------------------------------------------

    def _collect_npm(self, url: str, ctx, headers: dict[str, str]) -> PanelData:
        proxy_hosts = self._npm_fetch_hosts(url, ctx, headers, retry=True)

        warn_days = int(self.config.get("warn_days", 30))
        critical_days = int(self.config.get("critical_days", 7))
        now = time.time()

        hosts = []
        for h in proxy_hosts:
            domain = ", ".join(h.get("domain_names") or []) or "unknown"
            enabled = bool(h.get("enabled", True))
            online = (h.get("meta") or {}).get("nginx_online", True)
            if not enabled:
                status = "warn"  # deliberately disabled by the user, not a failure
            elif online is False:
                status = "error"
            else:
                status = "ok"

            cert_status = None
            cert_label = "—"
            expires_on = (h.get("certificate") or {}).get("expires_on")
            if expires_on:
                days = _npm_days_remaining(expires_on, now)
                if days is not None:
                    if days < critical_days:
                        cert_status = "error"
                        status = "error"
                    elif days <= warn_days:
                        cert_status = "warn"
                        status = "warn" if status == "ok" else status
                    else:
                        cert_status = "ok"
                    cert_label = "expired" if days < 0 else f"{days}d"

            hosts.append(
                {
                    "name": domain,
                    "status": status,
                    "detail": "",
                    "cert_status": cert_status,
                    "cert_label": cert_label,
                }
            )

        return self._make_panel("npm", hosts, error_rate=None, router_count=None)

    def _npm_fetch_hosts(self, url: str, ctx, headers: dict[str, str], retry: bool) -> list[dict]:
        token = self._npm_get_token(url, ctx)
        auth_headers = {**headers, "Authorization": f"Bearer {token}", "Accept": "application/json"}
        req = urllib.request.Request(
            f"{url}/api/nginx/proxy-hosts?expand=certificate", headers=auth_headers
        )
        try:
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry:
                self._npm_token = None
                self._npm_token_expiry = 0.0
                return self._npm_fetch_hosts(url, ctx, headers, retry=False)
            raise

    def _npm_get_token(self, url: str, ctx) -> str:
        if self._npm_token and time.time() < self._npm_token_expiry:
            return self._npm_token

        body = json.dumps(
            {
                "identity": self.config.get("username", ""),
                "secret": self.config.get("password", ""),
            }
        ).encode()
        req = urllib.request.Request(
            f"{url}/api/tokens",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read())

        self._npm_token = data.get("token", "")
        self._npm_token_expiry = time.time() + _NPM_TOKEN_TTL
        return self._npm_token

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _get_json(self, full_url: str, ctx, headers: dict[str, str]):
        req = urllib.request.Request(full_url, headers={**headers, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            return json.loads(resp.read())

    def _maybe_scrape_5xx_rate(self, ctx, headers: dict[str, str]) -> float | None:
        """Best-effort delta-based 5xx rate; None if unconfigured, unavailable,
        on the first sample, or across a counter reset (fresh baseline instead)."""
        metrics_url = self.config.get("metrics_url", "")
        if not metrics_url:
            return None

        try:
            req = urllib.request.Request(metrics_url, headers={**headers, "Accept": "text/plain"})
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            five_xx, total = _parse_5xx_counters(text)
        except Exception:
            return None

        prev = self._metrics_prev
        self._metrics_prev = (five_xx, total)
        if prev is None:
            return None

        d_total = total - prev[1]
        d_five = five_xx - prev[0]
        if d_total <= 0 or d_five < 0:
            return None  # counter reset, or no new traffic this cycle
        return (d_five / d_total) * 100

    def _make_panel(
        self,
        backend: str,
        hosts: list[dict],
        error_rate: float | None,
        router_count: int | None,
    ) -> PanelData:
        down = sum(1 for h in hosts if h["status"] == "error")
        warn = sum(1 for h in hosts if h["status"] == "warn")
        cert_expired = sum(1 for h in hosts if h.get("cert_status") == "error")
        cert_expiring = sum(1 for h in hosts if h.get("cert_status") == "warn")

        if down or cert_expired:
            status = "error"
        elif warn or cert_expiring:
            status = "warn"
        else:
            status = "ok"

        error_rate_warn = float(self.config.get("error_rate_warn", 5.0))
        if error_rate is not None:
            if error_rate >= error_rate_warn * 2:
                status = "error"
            elif error_rate > error_rate_warn and status == "ok":
                status = "warn"

        label = "routers" if router_count is not None else "hosts"
        count_shown = router_count if router_count is not None else len(hosts)
        parts = [f"{count_shown} {label}"]
        if error_rate is not None:
            parts.append(f"{error_rate:.1f}% 5xx")
        expiring_total = cert_expired + cert_expiring
        if expiring_total:
            plural = "s" if expiring_total != 1 else ""
            parts.append(f"{expiring_total} cert{plural} expiring")

        return PanelData(
            status=status,
            summary=" · ".join(parts),
            detail={
                "backend": backend,
                "router_count": count_shown,
                "hosts": hosts[:_MAX_ROWS],
                "host_total": len(hosts),
                "error_rate": error_rate,
            },
        )

    def demo_data(self) -> PanelData:
        hosts = [
            {
                "name": "app.example.com",
                "status": "ok",
                "detail": "Host(`app.example.com`)",
                "cert_status": "ok",
                "cert_label": "TLS",
            },
            {
                "name": "grafana.example.com",
                "status": "ok",
                "detail": "Host(`grafana.example.com`)",
                "cert_status": "warn",
                "cert_label": "18d",
            },
            {
                "name": "legacy.example.com",
                "status": "warn",
                "detail": "Host(`legacy.example.com`)",
                "cert_status": None,
                "cert_label": "no TLS",
            },
        ]
        return self._make_panel("traefik", hosts, error_rate=0.4, router_count=len(hosts))

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        hosts = d.get("hosts") or []
        if not hosts:
            return [panel.text("No routes found", status="dim")]

        label = "Routers" if d.get("backend") == "traefik" else "Hosts"
        rows = [{"label": label, "value": str(d.get("router_count", len(hosts)))}]
        error_rate = d.get("error_rate")
        if error_rate is not None:
            error_rate_warn = float(self.config.get("error_rate_warn", 5.0))
            rows.append(
                {
                    "label": "5xx rate",
                    "value": f"{error_rate:.1f}%",
                    "status": "warn" if error_rate > error_rate_warn else "ok",
                }
            )
        blocks: list[dict] = [panel.keyvalue(rows)]

        table_rows = [
            [
                panel.cell(h.get("name", ""), truncate=True),
                panel.cell(h.get("status", "ok"), status=h.get("status")),
                panel.cell(h.get("cert_label", "—"), status=h.get("cert_status")),
            ]
            for h in hosts
        ]
        blocks.append(panel.table(["Host", "Status", "Cert"], table_rows))

        host_total = d.get("host_total", len(hosts))
        remaining = host_total - len(hosts)
        if remaining > 0:
            blocks.append(panel.text(f"+{remaining} more", status="dim"))
        return blocks
