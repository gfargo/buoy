"""Trigger.dev plugin — task run status: failures, queue depth, worker health proxy."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

# Statuses considered failures
_FAILURE_STATUSES = ("FAILED", "CRASHED", "SYSTEM_FAILURE", "INTERRUPTED", "TIMED_OUT")


class TriggerDevPlugin(Plugin):
    """Shows recent Trigger.dev task run failures and queue depth."""

    manifest = PluginManifest(
        id="trigger_dev",
        name="Tasks",
        icon="⚡",
        description="Trigger.dev task run status",
        version="1.0.0",
        config_schema={
            "url": {"type": "string"},
            "api_key": {"type": "string", "required": True},
            "project_ref": {"type": "string"},
            "queue_threshold": {"type": "integer"},
        },
        refresh_interval=60,
    )

    def _fetch(self, base_url: str, api_key: str, params: dict) -> dict:
        qs = urllib.parse.urlencode(params)
        req = urllib.request.Request(
            f"{base_url}/api/v1/runs?{qs}",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    async def collect(self) -> PanelData:
        api_key = self.config.get("api_key", "")
        if not api_key:
            return PanelData(status="disabled", summary="Not configured")

        base_url = self.config.get("url", "https://api.trigger.dev").rstrip("/")
        project_ref = self.config.get("project_ref", "")
        queue_threshold = int(self.config.get("queue_threshold", 50))

        try:
            # --- failed runs in last 24h ---
            failures_data = self._fetch(
                base_url,
                api_key,
                {
                    "filter[status]": ",".join(_FAILURE_STATUSES),
                    "filter[createdAt][period]": "1d",
                    "page[size]": 20,
                },
            )
            failed_runs = failures_data.get("data", [])
            failed_count = len(failed_runs)
            last_failed_task = failed_runs[0]["taskIdentifier"] if failed_runs else None

            # --- queue depth ---
            queued_data = self._fetch(
                base_url,
                api_key,
                {"filter[status]": "QUEUED", "page[size]": 100},
            )
            queue_depth = len(queued_data.get("data", []))

        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

        if queue_depth > queue_threshold:
            status = "error"
            summary = f"Queue: {queue_depth} pending"
        elif failed_count > 0:
            status = "warn"
            summary = f"{failed_count} failed (24h)"
        else:
            status = "ok"
            summary = "All clear"

        detail: dict = {
            "failed_count": failed_count,
            "queue_depth": queue_depth,
        }
        if last_failed_task:
            detail["last_failed_task"] = last_failed_task
        if project_ref:
            detail["project_ref"] = project_ref
        detail["recent_failures"] = [
            {"task": r["taskIdentifier"], "id": r["id"], "at": r.get("createdAt", "")}
            for r in failed_runs[:5]
        ]

        return PanelData(status=status, summary=summary, detail=detail)

    def frontend_js(self) -> str | None:
        return """
function render_trigger_dev(data) {
  const d = data.detail || {};
  const failed = d.failed_count || 0;
  const queued = d.queue_depth || 0;
  const failColor = failed > 0 ? 'var(--amber)' : 'var(--text-dim)';
  const queueColor = queued > 50 ? 'var(--red)' : 'var(--text-dim)';
  let html = '<div style="font-size:0.6rem;display:flex;gap:1rem;padding:0.3rem 0">'
    + '<span style="color:' + failColor + '">' + failed + ' failed (24h)</span>'
    + '<span style="color:' + queueColor + '">' + queued + ' queued</span>'
    + '</div>';
  if (d.last_failed_task) {
    html += '<div style="font-size:0.5rem;color:var(--text-dim);margin-top:0.2rem">Last: ' + d.last_failed_task + '</div>';
  }
  return html;
}
"""
