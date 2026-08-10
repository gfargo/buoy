"""Loki plugin — recent error log entries from Loki query API."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class LokiPlugin(Plugin):
    """Shows recent error/fatal log entries from Loki."""

    manifest = PluginManifest(
        id="loki",
        name="Errors",
        icon="🔴",
        description="Recent error logs from Loki",
        version="1.0.0",
        config_schema={"url": {"type": "string", "required": True}},
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "")
        if not url:
            return PanelData(status="disabled", summary="Not configured")

        try:
            query = '{job=~".+"} |~ "(?i)error|fatal|crit"'
            params = urllib.parse.urlencode(
                {"query": query, "limit": "15", "direction": "backward"}
            )
            api_url = f"{url.rstrip('/')}/loki/api/v1/query_range?{params}"
            req = urllib.request.Request(api_url)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())

            entries = []
            for stream in data.get("data", {}).get("result", []):
                labels = stream.get("stream", {})
                for ts, line in stream.get("values", [])[:15]:
                    entries.append(
                        {
                            "ts": ts,
                            "line": line[:200],
                            "job": labels.get("job", ""),
                        }
                    )

            count = len(entries)
            status = "warn" if count > 0 else "ok"
            summary = f"{count} recent error{'s' if count != 1 else ''}" if count else "No errors"

            return PanelData(status=status, summary=summary, detail={"entries": entries[:10]})
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    def render(self, data: PanelData) -> list[dict] | None:
        entries = data.detail.get("entries") or []
        if not entries:
            return [panel.text("No recent errors", status="dim")]

        items = []
        for e in entries:
            ts = e.get("ts")
            time_label = _fmt_ns_timestamp(ts) if ts else ""
            secondary = f"{time_label} · {e.get('job', '')}" if time_label else e.get("job", "")
            items.append(panel.list_item(e.get("line", ""), secondary=secondary, status="error"))
        return [panel.list_(items)]


def _fmt_ns_timestamp(ts: str) -> str:
    """Format a Loki nanosecond-epoch timestamp string as HH:MM:SS UTC."""
    try:
        seconds = int(ts) / 1_000_000_000
        return datetime.fromtimestamp(seconds, tz=UTC).strftime("%H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return ""
