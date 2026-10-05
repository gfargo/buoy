"""Ban status plugin — Fail2ban / CrowdSec active bans, decisions, top offenders."""

from __future__ import annotations

import asyncio
import json
import re

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest
from buoy.subprocess_utils import communicate


class BanStatusPlugin(Plugin):
    """Shows active bans/decisions from Fail2ban or CrowdSec via nsenter."""

    manifest = PluginManifest(
        id="ban_status",
        name="Bans",
        icon="🚫",
        description="Fail2ban / CrowdSec active bans and recent decisions",
        version="1.0.0",
        config_schema={
            "type": {"type": "string", "default": "fail2ban"},  # fail2ban | crowdsec
            "warn_threshold": {"type": "integer", "default": 25},
            "max_offenders": {"type": "integer", "default": 5},
            "max_decisions": {"type": "integer", "default": 10},
        },
        refresh_interval=120,
    )

    async def collect(self) -> PanelData:
        backend = self.config.get("type", "fail2ban")
        if backend == "fail2ban":
            return await self._collect_fail2ban()
        elif backend == "crowdsec":
            return await self._collect_crowdsec()
        return PanelData(status="error", summary=f"Unknown type: {backend!r}")

    async def _run(self, argv: list[str]) -> tuple[int, str] | None:
        """Run argv in the host mount namespace; None if the tool is unreachable."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "nsenter",
                "-t",
                "1",
                "-m",
                "--",
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await communicate(proc, timeout=5)
        except (TimeoutError, FileNotFoundError):
            return None
        return proc.returncode, stdout.decode(errors="replace")

    # ------------------------------------------------------------------
    # Fail2ban
    # ------------------------------------------------------------------

    async def _collect_fail2ban(self) -> PanelData:
        warn_threshold = int(self.config.get("warn_threshold", 25))
        max_offenders = int(self.config.get("max_offenders", 5))

        result = await self._run(["fail2ban-client", "status"])
        if result is None or result[0] != 0:
            return PanelData(
                status="error",
                summary="fail2ban not running",
                detail={"backend": "fail2ban"},
            )

        jail_names = _parse_jail_list(result[1])

        jail_results = await asyncio.gather(
            *[self._run(["fail2ban-client", "status", jail]) for jail in jail_names]
        )

        jails: list[dict] = []
        banned_ips_by_jail: dict[str, list[str]] = {}
        for jail_name, jail_result in zip(jail_names, jail_results):
            if jail_result is None or jail_result[0] != 0:
                continue
            parsed = _parse_jail_status(jail_result[1])
            jails.append({"jail": jail_name, **parsed["counts"]})
            banned_ips_by_jail[jail_name] = parsed["banned_ips"]

        total_banned = sum(j.get("currently_banned", 0) for j in jails)
        top_offenders = _rank_offenders(banned_ips_by_jail, max_offenders)

        status = "warn" if total_banned >= warn_threshold else "ok"
        summary = f"{total_banned} banned · {len(jails)} jails"
        return PanelData(
            status=status,
            summary=summary,
            detail={
                "backend": "fail2ban",
                "total_banned": total_banned,
                "jails": jails,
                "top_offenders": top_offenders,
            },
        )

    # ------------------------------------------------------------------
    # CrowdSec
    # ------------------------------------------------------------------

    async def _collect_crowdsec(self) -> PanelData:
        warn_threshold = int(self.config.get("warn_threshold", 25))
        max_offenders = int(self.config.get("max_offenders", 5))
        max_decisions = int(self.config.get("max_decisions", 10))

        result = await self._run(["cscli", "decisions", "list", "-o", "json"])
        if result is None or result[0] != 0:
            return PanelData(
                status="error",
                summary="CrowdSec not running",
                detail={"backend": "crowdsec"},
            )

        try:
            payload = json.loads(result[1]) if result[1].strip() else None
        except json.JSONDecodeError:
            return PanelData(
                status="error",
                summary="CrowdSec output unparsable",
                detail={"backend": "crowdsec"},
            )

        raw_decisions = _flatten_decisions(payload or [])
        total = len(raw_decisions)

        offender_counts: dict[str, int] = {}
        for d in raw_decisions:
            ip = d.get("value", "")
            if ip:
                offender_counts[ip] = offender_counts.get(ip, 0) + 1
        top_offenders = [
            {"ip": ip, "count": count}
            for ip, count in sorted(offender_counts.items(), key=lambda kv: -kv[1])[:max_offenders]
        ]

        decisions = [
            {
                "ip": d.get("value", ""),
                "scenario": d.get("scenario", ""),
                "type": d.get("type", ""),
                "duration": d.get("duration", ""),
            }
            for d in raw_decisions[:max_decisions]
        ]

        status = "warn" if total >= warn_threshold else "ok"
        summary = f"{total} active decisions"
        return PanelData(
            status=status,
            summary=summary,
            detail={
                "backend": "crowdsec",
                "total_banned": total,
                "decisions": decisions,
                "top_offenders": top_offenders,
            },
        )

    # ------------------------------------------------------------------
    # Demo / render
    # ------------------------------------------------------------------

    def demo_data(self) -> PanelData:
        jails = [
            {"jail": "sshd", "currently_banned": 9, "total_banned": 41, "currently_failed": 2},
            {
                "jail": "nginx-http-auth",
                "currently_banned": 5,
                "total_banned": 18,
                "currently_failed": 0,
            },
        ]
        top_offenders = [
            {"ip": "203.0.113.42", "jails": ["sshd", "nginx-http-auth"], "count": 2},
            {"ip": "198.51.100.7", "jails": ["sshd"], "count": 1},
        ]
        return PanelData(
            status="warn",
            summary="14 banned · 2 jails",
            detail={
                "backend": "fail2ban",
                "total_banned": 14,
                "jails": jails,
                "top_offenders": top_offenders,
            },
        )

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        backend = d.get("backend", "fail2ban")

        if backend == "crowdsec":
            return self._render_crowdsec(d)
        return self._render_fail2ban(d)

    def _render_fail2ban(self, d: dict) -> list[dict]:
        jails = d.get("jails") or []
        top_offenders = d.get("top_offenders") or []

        if not jails:
            return [panel.text("No active bans", status="dim")]

        blocks: list[dict] = [
            panel.keyvalue(
                [
                    {
                        "label": "Active bans",
                        "value": str(d.get("total_banned", 0)),
                        "status": "warn",
                    },
                    {"label": "Jails", "value": str(len(jails)), "status": "info"},
                ]
            ),
            panel.table(
                ["Jail", "Banned", "Failed", "Total"],
                [
                    [
                        panel.cell(j.get("jail", "")),
                        panel.cell(j.get("currently_banned", 0), status="warn"),
                        panel.cell(j.get("currently_failed", 0)),
                        panel.cell(j.get("total_banned", 0), status="dim"),
                    ]
                    for j in jails
                ],
            ),
        ]
        if top_offenders:
            blocks.append(
                panel.list_(
                    [
                        panel.list_item(o.get("ip", ""), secondary=f"{o.get('count', 0)} jails")
                        for o in top_offenders
                    ]
                )
            )
        return blocks

    def _render_crowdsec(self, d: dict) -> list[dict]:
        decisions = d.get("decisions") or []
        top_offenders = d.get("top_offenders") or []

        if not decisions:
            return [panel.text("No active bans", status="dim")]

        blocks: list[dict] = [
            panel.keyvalue(
                [
                    {
                        "label": "Active decisions",
                        "value": str(d.get("total_banned", 0)),
                        "status": "warn",
                    },
                ]
            ),
            panel.table(
                ["IP", "Scenario", "Duration"],
                [
                    [
                        panel.cell(dec.get("ip", ""), mono=True),
                        panel.cell(dec.get("scenario", ""), truncate=True),
                        panel.cell(dec.get("duration", ""), status="dim"),
                    ]
                    for dec in decisions
                ],
            ),
        ]
        if top_offenders:
            blocks.append(
                panel.list_(
                    [
                        panel.list_item(o.get("ip", ""), secondary=f"{o.get('count', 0)} decisions")
                        for o in top_offenders
                    ]
                )
            )
        return blocks


# ------------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------------


def _parse_jail_list(status_output: str) -> list[str]:
    """Parse the "Jail list:" line out of `fail2ban-client status` output."""
    for line in status_output.splitlines():
        if "Jail list:" in line:
            _, _, rest = line.partition("Jail list:")
            return [j.strip() for j in rest.strip().split(",") if j.strip()]
    return []


_COUNT_PATTERNS = {
    "currently_failed": re.compile(r"Currently failed:\s*(\d+)"),
    "total_failed": re.compile(r"Total failed:\s*(\d+)"),
    "currently_banned": re.compile(r"Currently banned:\s*(\d+)"),
    "total_banned": re.compile(r"Total banned:\s*(\d+)"),
}
_BANNED_IP_LIST_RE = re.compile(r"Banned IP list:[ \t]*(.*)")


def _parse_jail_status(status_output: str) -> dict:
    """Parse `fail2ban-client status <jail>` output into counts + banned IPs."""
    counts = {}
    for key, pattern in _COUNT_PATTERNS.items():
        match = pattern.search(status_output)
        counts[key] = int(match.group(1)) if match else 0

    banned_ips: list[str] = []
    ip_match = _BANNED_IP_LIST_RE.search(status_output)
    if ip_match:
        banned_ips = [ip for ip in ip_match.group(1).strip().split() if ip]

    return {"counts": counts, "banned_ips": banned_ips}


def _rank_offenders(banned_ips_by_jail: dict[str, list[str]], limit: int) -> list[dict]:
    """Rank IPs by how many jails they appear in, then by first-seen order."""
    jails_by_ip: dict[str, list[str]] = {}
    for jail, ips in banned_ips_by_jail.items():
        for ip in ips:
            jails_by_ip.setdefault(ip, []).append(jail)

    ranked = sorted(jails_by_ip.items(), key=lambda kv: -len(kv[1]))
    return [{"ip": ip, "jails": jails, "count": len(jails)} for ip, jails in ranked[:limit]]


def _flatten_decisions(payload: list) -> list[dict]:
    """Flatten `cscli decisions list -o json` output across schema variants.

    Newer CrowdSec versions nest decisions under each alert's ``decisions``
    key; treat an element without that key as a decision itself.
    """
    decisions: list[dict] = []
    for entry in payload:
        if isinstance(entry, dict) and isinstance(entry.get("decisions"), list):
            decisions.extend(entry["decisions"])
        elif isinstance(entry, dict):
            decisions.append(entry)
    return decisions
