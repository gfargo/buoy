"""Trigger.dev plugin — task run status: recent runs, failure count, queue depth."""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime, timedelta

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_MAX_RUNS = 50
_QUEUE_WARN_THRESHOLD = 50
# Statuses that count as failures (excluding CANCELED which is intentional)
_FAILED_STATUSES = {"FAILED", "CRASHED", "TIMED_OUT", "INTERRUPTED", "SYSTEM_FAILURE"}
# Statuses that indicate work backed up in queue
_QUEUED_STATUSES = {"QUEUED", "EXECUTING", "REATTEMPTING", "PENDING_VERSION", "DELAYED"}


class TriggerDevPlugin(Plugin):
    """Shows Trigger.dev task run health: recent runs, 24h failures, queue depth."""

    manifest = PluginManifest(
        id="trigger_dev",
        name="Trigger.dev",
        icon="⚡",
        description="Task run status",
        version="1.0.0",
        config_schema={
            "url": {"type": "string", "required": True},
            "api_key": {"type": "string", "required": True},
            "project_ref": {"type": "string", "required": True},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "").rstrip("/")
        api_key = self.config.get("api_key", "")
        project_ref = self.config.get("project_ref", "")
        queue_warn_threshold = self.config.get("queue_warn_threshold", _QUEUE_WARN_THRESHOLD)

        if not url or not api_key or not project_ref:
            return PanelData(status="disabled", summary="Not configured")

        try:
            api_url = (
                f"{url}/api/v1/runs"
                f"?page[size]={_MAX_RUNS}"
                f"&filter[projectRef]={project_ref}"
            )
            req = urllib.request.Request(
                api_url,
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())

            runs = data.get("data", [])
            cutoff = datetime.now(tz=UTC) - timedelta(hours=24)

            failures_24h = 0
            queued = 0
            last_failed: str | None = None
            recent: list[dict] = []

            for run in runs:
                status = run.get("status", "")
                task_id = run.get("taskIdentifier", "")

                if status in _QUEUED_STATUSES:
                    queued += 1

                if status in _FAILED_STATUSES:
                    finished_raw = run.get("finishedAt") or run.get("updatedAt") or ""
                    try:
                        finished = datetime.fromisoformat(
                            finished_raw.replace("Z", "+00:00")
                        )
                    except (ValueError, AttributeError):
                        finished = None

                    if finished and finished >= cutoff:
                        failures_24h += 1
                        if last_failed is None:
                            last_failed = task_id

                recent.append({"id": run.get("id", ""), "task": task_id, "status": status})

            if queued > queue_warn_threshold:
                panel_status = "error"
                summary = f"Queue: {queued} pending"
            elif failures_24h > 0:
                panel_status = "warn"
                summary = f"{failures_24h} failed (24h)"
            else:
                panel_status = "ok"
                summary = f"OK · {len(runs)} runs"

            return PanelData(
                status=panel_status,
                summary=summary,
                detail={
                    "recent": recent,
                    "failures_24h": failures_24h,
                    "queued": queued,
                    "last_failed": last_failed,
                },
            )
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    def frontend_js(self) -> str | None:
        return """
function render_trigger_dev(data) {
  const d = data.detail || {};
  const recent = d.recent || [];
  const lastFailed = d.last_failed;
  const failures = d.failures_24h || 0;
  const queued = d.queued || 0;
  const statusColor = data.status === 'ok' ? 'var(--green)' : data.status === 'warn' ? 'var(--amber)' : 'var(--red)';
  let html = '<div style="font-size:0.6rem;margin-bottom:0.4rem">';
  html += '<span style="color:' + statusColor + '">' + data.summary + '</span>';
  if (lastFailed) {
    html += '<span style="color:var(--text-dim);margin-left:0.5rem">last failed: <strong style="color:var(--amber)">' + lastFailed + '</strong></span>';
  }
  if (queued > 0) {
    html += '<span style="color:var(--cyan);margin-left:0.5rem">' + queued + ' queued</span>';
  }
  html += '</div>';
  if (recent.length) {
    html += '<div style="display:flex;flex-wrap:wrap;gap:0.25rem">' + recent.slice(0, 10).map(r => {
      const c = r.status === 'COMPLETED' ? 'var(--green)' : (r.status === 'FAILED' || r.status === 'CRASHED') ? 'var(--red)' : (r.status === 'QUEUED' || r.status === 'EXECUTING') ? 'var(--cyan)' : 'var(--text-dim)';
      return '<div style="font-size:0.5rem;padding:0.1rem 0.35rem;border:1px solid var(--border);border-radius:3px;color:' + c + '">' + r.task + '</div>';
    }).join('') + '</div>';
  }
  return html || '<div style="font-size:0.6rem;color:var(--text-dim)">No data</div>';
}
"""
