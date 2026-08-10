"""GitHub plugin — notifications and open PRs."""

from __future__ import annotations

import json
import urllib.request

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class GitHubPlugin(Plugin):
    """Shows GitHub notifications and open PRs for the authenticated user."""

    manifest = PluginManifest(
        id="github",
        name="GitHub",
        icon="🐙",
        description="Notifications & open PRs",
        version="1.0.0",
        config_schema={"token": {"type": "string", "required": True, "env": "BUOY_GITHUB_TOKEN"}},
        refresh_interval=300,
    )

    async def collect(self) -> PanelData:
        token = self.config.get("token", "")
        if not token:
            return PanelData(status="disabled", summary="Not configured")

        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        }

        try:
            # Notifications
            req = urllib.request.Request(
                "https://api.github.com/notifications?per_page=10", headers=headers
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                notifications = json.loads(resp.read())

            # Open PRs
            pr_req = urllib.request.Request(
                "https://api.github.com/search/issues?q=is:pr+is:open+author:@me&per_page=5",
                headers=headers,
            )
            with urllib.request.urlopen(pr_req, timeout=8) as resp:
                prs = json.loads(resp.read())

            notif_count = len(notifications)
            pr_count = prs.get("total_count", 0)

            summary_parts = []
            if notif_count:
                summary_parts.append(f"{notif_count} notification{'s' if notif_count != 1 else ''}")
            if pr_count:
                summary_parts.append(f"{pr_count} open PR{'s' if pr_count != 1 else ''}")
            summary = ", ".join(summary_parts) or "All clear"

            return PanelData(
                status="ok" if notif_count == 0 else "warn",
                summary=summary,
                detail={
                    "notifications": [
                        {
                            "title": n["subject"]["title"],
                            "repo": n["repository"]["full_name"],
                            "type": n["subject"]["type"],
                        }
                        for n in notifications[:5]
                    ],
                    "notification_count": notif_count,
                    "open_prs": [
                        {"title": p["title"], "url": p["html_url"]}
                        for p in prs.get("items", [])[:5]
                    ],
                    "pr_count": pr_count,
                },
            )
        except Exception as e:
            return PanelData(status="error", summary="API error", detail={"error": str(e)})

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        notif_count = d.get("notification_count", 0)
        pr_count = d.get("pr_count", 0)

        if not notif_count and not pr_count:
            return [panel.text("All clear", status="dim")]

        blocks: list[dict] = []
        if notif_count:
            blocks.append(
                panel.list_(
                    [
                        panel.list_item(
                            f"{n.get('type', '')}: {n.get('title', '')}",
                            secondary=n.get("repo", ""),
                        )
                        for n in d.get("notifications") or []
                    ]
                )
            )
        if pr_count:
            blocks.append(
                panel.list_(
                    [
                        panel.list_item(p.get("title", ""), status="info", href=p.get("url"))
                        for p in d.get("open_prs") or []
                    ]
                )
            )
        return blocks
