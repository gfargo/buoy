"""DNS Filter plugin — Pi-hole or AdGuard Home DNS filtering stats."""

from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_WARN_PCT = 25.0


class DnsFilterPlugin(Plugin):
    """Shows DNS filtering stats from Pi-hole (v5/v6) or AdGuard Home."""

    manifest = PluginManifest(
        id="dns_filter",
        name="DNS Filter",
        icon="🛡️",
        description="Pi-hole (v5 & v6) / AdGuard Home DNS filtering stats",
        version="1.1.0",
        config_schema={
            "type": {"type": "string", "required": True},  # pihole | adguard
            "url": {"type": "string", "required": True},
            "api_key": {"type": "string"},  # Pi-hole v5: topItems token; AdGuard: base64 user:pass
            "username": {"type": "string"},  # AdGuard Basic-auth username
            "password": {"type": "string"},  # Pi-hole v6 password or AdGuard Basic-auth password
            "version": {"type": "string"},  # Pi-hole version hint: "auto" (default) | "5" | "6"
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

    # ------------------------------------------------------------------
    # Pi-hole dispatcher — auto-detects v5 vs v6
    # ------------------------------------------------------------------

    async def _collect_pihole(self, url: str, ctx) -> PanelData:
        version_hint = str(self.config.get("version", "auto")).lower()

        if version_hint == "5":
            return self._collect_pihole_v5(url, ctx)
        elif version_hint == "6":
            return self._collect_pihole_v6(url, ctx)

        # Auto-detect: probe the v6-only /api/info/version endpoint
        pihole_version = self._detect_pihole_version(url, ctx)
        if pihole_version == 6:
            return self._collect_pihole_v6(url, ctx)
        return self._collect_pihole_v5(url, ctx)

    def _detect_pihole_version(self, url: str, ctx) -> int:
        """Return 6 if /api/info/version responds, 5 otherwise."""
        try:
            req = urllib.request.Request(
                f"{url}/api/info/version",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                if resp.status == 200:
                    return 6
        except urllib.error.HTTPError as e:
            # 401 means v6 is present but password-protected
            if e.code == 401:
                return 6
        except Exception:
            pass
        return 5

    # ------------------------------------------------------------------
    # Pi-hole v5 (legacy API)
    # ------------------------------------------------------------------

    def _collect_pihole_v5(self, url: str, ctx) -> PanelData:
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
                    {"domain": d, "count": c}
                    for d, c in (top_data.get("top_ads") or {}).items()
                ]
            except Exception:
                pass  # top domains are best-effort

        return self._make_panel(queries, blocked, pct, top_blocked)

    # ------------------------------------------------------------------
    # Pi-hole v6 (REST API with session auth)
    # ------------------------------------------------------------------

    def _pihole_v6_auth(self, url: str, ctx) -> str | None:
        """Authenticate to Pi-hole v6 and return a session ID (SID).

        Returns None and lets the caller handle the error case when auth
        cannot be completed — but a missing/invalid password is returned
        as a PanelData error by _collect_pihole_v6 instead.
        """
        password = self.config.get("password", "") or ""
        body = json.dumps({"password": password}).encode()
        req = urllib.request.Request(
            f"{url}/api/auth",
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            auth_data = json.loads(resp.read())

        session = auth_data.get("session", {})
        if not session.get("valid", False):
            return None
        return session.get("sid")

    def _pihole_v6_logout(self, url: str, ctx, sid: str) -> None:
        """Release the Pi-hole v6 session (best-effort; ignores errors)."""
        try:
            req = urllib.request.Request(
                f"{url}/api/auth",
                headers={"X-FTL-SID": sid},
                method="DELETE",
            )
            urllib.request.urlopen(req, timeout=5, context=ctx).close()
        except Exception:
            pass

    def _collect_pihole_v6(self, url: str, ctx) -> PanelData:
        # Authenticate (empty password is valid for open instances)
        try:
            sid = self._pihole_v6_auth(url, ctx)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return PanelData(status="error", summary="Pi-hole auth failed (check password)")
            raise
        if sid is None:
            return PanelData(status="error", summary="Pi-hole auth failed (check password)")

        try:
            # Fetch summary stats
            req = urllib.request.Request(
                f"{url}/api/stats/summary",
                headers={"Accept": "application/json", "X-FTL-SID": sid},
            )
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                summary = json.loads(resp.read())

            q = summary.get("queries", {})
            queries = int(q.get("total", 0))
            blocked = int(q.get("blocked", 0))
            pct = float(q.get("percent_blocked", 0.0))

            # Best-effort top blocked domains
            top_blocked: list[dict] = []
            try:
                top_req = urllib.request.Request(
                    f"{url}/api/stats/top_domains?blocked=true",
                    headers={"Accept": "application/json", "X-FTL-SID": sid},
                )
                with urllib.request.urlopen(top_req, timeout=8, context=ctx) as resp:
                    top_data = json.loads(resp.read())
                # v6 returns {"blocked": [{"domain": "...", "count": N}, ...]}
                for entry in top_data.get("blocked", []):
                    top_blocked.append(
                        {"domain": entry.get("domain", ""), "count": entry.get("count", 0)}
                    )
            except Exception:
                pass  # top domains are best-effort

        finally:
            self._pihole_v6_logout(url, ctx, sid)

        # If filtering is disabled the API still returns 0s without an explicit flag;
        # treat all-zero with no query history as a disabled/error state only if the
        # summary explicitly carries a status field saying "disabled".
        if summary.get("status") == "disabled":
            return PanelData(status="error", summary="Filtering disabled")

        return self._make_panel(queries, blocked, pct, top_blocked)

    # ------------------------------------------------------------------
    # AdGuard Home
    # ------------------------------------------------------------------

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
            {"domain": d, "count": c}
            for d, c in (data.get("top_blocked_domains") or {}).items()
        ]

        return self._make_panel(queries, blocked, pct, top_blocked)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

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

    def demo_data(self) -> PanelData:
        top_blocked = [
            {"domain": "ads.example.com", "count": 812},
            {"domain": "track.adtech.net", "count": 645},
            {"domain": "telemetry.badcorp.io", "count": 301},
        ]
        return self._make_panel(queries=48213, blocked=9021, pct=18.7, top_blocked=top_blocked)

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        top = d.get("top_blocked") or []
        blocks: list[dict] = [
            panel.keyvalue(
                [
                    {"label": "Queries", "value": f"{d.get('queries', 0):,}", "status": "info"},
                    {
                        "label": "Blocked",
                        "value": f"{d.get('pct', 0)}% ({d.get('blocked', 0):,})",
                        "status": "warn",
                    },
                ]
            )
        ]
        if top:
            blocks.append(
                panel.list_(
                    [
                        panel.list_item(t.get("domain", ""), secondary=str(t.get("count", "")))
                        for t in top[:5]
                    ]
                )
            )
        return blocks
