"""Trigger.dev plugin — task run status: recent runs, failure count, queue depth."""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime, timedelta

from buoy.plugins import panel
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
            api_url = f"{url}/api/v1/runs?page[size]={_MAX_RUNS}&filter[projectRef]={project_ref}"
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
                        finished = datetime.fromisoformat(finished_raw.replace("Z", "+00:00"))
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

    def demo_data(self) -> PanelData:
        recent = [
            {"id": "run_1", "task": "sync-photos", "status": "COMPLETED"},
            {"id": "run_2", "task": "prune-backups", "status": "COMPLETED"},
            {"id": "run_3", "task": "sync-photos", "status": "FAILED"},
            {"id": "run_4", "task": "check-updates", "status": "QUEUED"},
        ]
        return PanelData(
            status="warn",
            summary="1 failed (24h)",
            detail={"recent": recent, "failures_24h": 1, "queued": 1, "last_failed": "sync-photos"},
        )

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        recent = d.get("recent") or []
        last_failed = d.get("last_failed")
        queued = d.get("queued") or 0

        blocks: list[dict] = []
        kv_rows = []
        if last_failed:
            kv_rows.append({"label": "Last failed", "value": last_failed, "status": "warn"})
        if queued > 0:
            kv_rows.append({"label": "Queued", "value": str(queued), "status": "info"})
        if kv_rows:
            blocks.append(panel.keyvalue(kv_rows))

        if not recent:
            blocks.append(panel.text("No data", status="dim"))
            return blocks

        run_status_map = {
            "COMPLETED": "ok",
            "FAILED": "error",
            "CRASHED": "error",
            "QUEUED": "info",
            "EXECUTING": "info",
        }
        blocks.append(
            panel.badges(
                [
                    panel.badge(
                        r.get("task", ""),
                        status=run_status_map.get(r.get("status"), "dim"),
                        dot=False,
                    )
                    for r in recent[:10]
                ]
            )
        )
        return blocks
