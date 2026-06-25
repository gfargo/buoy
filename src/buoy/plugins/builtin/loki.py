"""Loki plugin — recent error log entries from Loki query API."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

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
            params = urllib.parse.urlencode({"query": query, "limit": "15", "direction": "backward"})
            api_url = f"{url.rstrip('/')}/loki/api/v1/query_range?{params}"
            req = urllib.request.Request(api_url)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())

            entries = []
            for stream in data.get("data", {}).get("result", []):
                labels = stream.get("stream", {})
                for ts, line in stream.get("values", [])[:15]:
                    entries.append({
                        "ts": ts,
                        "line": line[:200],
                        "job": labels.get("job", ""),
                    })

            count = len(entries)
            status = "warn" if count > 0 else "ok"
            summary = f"{count} recent error{'s' if count != 1 else ''}" if count else "No errors"

            return PanelData(status=status, summary=summary, detail={"entries": entries[:10]})
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    def frontend_js(self) -> str | None:
        return """
function render_loki(data) {
  const entries = data.detail.entries || [];
  if (!entries.length) return '<div style="font-size:0.6rem;color:var(--text-dim)">No recent errors</div>';
  return '<div style="max-height:150px;overflow-y:auto">' + entries.map(e => {
    const ts = e.ts ? new Date(parseInt(e.ts) / 1000000).toLocaleTimeString() : '';
    return '<div style="font-size:0.5rem;margin-bottom:0.3rem;padding:0.3rem 0.5rem;border:1px solid var(--border);border-radius:3px"><div style="color:var(--text-dim);font-size:0.45rem">' + ts + ' · ' + e.job + '</div><div style="color:var(--red);word-break:break-all">' + e.line + '</div></div>';
  }).join('') + '</div>';
}
"""
