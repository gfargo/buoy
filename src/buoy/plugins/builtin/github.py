"""GitHub plugin — notifications and open PRs."""

from __future__ import annotations

import json
import urllib.request

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
                        {"title": n["subject"]["title"], "repo": n["repository"]["full_name"], "type": n["subject"]["type"]}
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

    def frontend_js(self) -> str | None:
        return """
function render_github(data) {
  let html = '';
  if (data.detail.notification_count > 0) {
    html += '<div style="font-size:0.6rem;color:var(--amber);margin-bottom:0.4rem">' + data.detail.notification_count + ' notifications</div>';
    (data.detail.notifications || []).forEach(n => {
      html += '<div style="font-size:0.55rem;color:var(--text);margin-bottom:0.2rem">' + n.type + ': ' + n.title + ' <span style="color:var(--text-dim)">' + n.repo + '</span></div>';
    });
  }
  if (data.detail.pr_count > 0) {
    html += '<div style="font-size:0.55rem;color:var(--text-dim);margin-top:0.4rem">' + data.detail.pr_count + ' open PRs</div>';
    (data.detail.open_prs || []).forEach(p => {
      html += '<div style="font-size:0.55rem;margin-bottom:0.2rem"><a href="' + p.url + '" style="color:var(--cyan);text-decoration:none">' + p.title + '</a></div>';
    });
  }
  if (!data.detail.notification_count && !data.detail.pr_count) {
    html += '<div style="font-size:0.6rem;color:var(--text-dim)">All clear</div>';
  }
  return html;
}
"""
