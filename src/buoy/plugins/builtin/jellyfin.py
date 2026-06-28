"""Jellyfin plugin — media server status: active streams, libraries, transcoding."""

from __future__ import annotations

import json
import urllib.request

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class JellyfinPlugin(Plugin):
    """Shows Jellyfin media server status: active streams and library info."""

    manifest = PluginManifest(
        id="jellyfin",
        name="Jellyfin",
        icon="🎬",
        description="Media server status",
        version="1.0.0",
        config_schema={
            "url": {"type": "string", "required": True},
            # X-Emby-Token is the broadly supported auth header for Jellyfin instances
            "api_key": {"type": "string", "required": True},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "").rstrip("/")
        api_key = self.config.get("api_key", "")
        if not url or not api_key:
            return PanelData(status="disabled", summary="Not configured")

        headers = {"X-Emby-Token": api_key, "Accept": "application/json"}

        try:
            # Fetch active sessions
            req = urllib.request.Request(f"{url}/Sessions", headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                sessions = json.loads(resp.read())

            # A session is an active stream only if something is playing
            active_sessions = [s for s in sessions if s.get("NowPlayingItem")]
            active = len(active_sessions)
            # TranscodingInfo presence is the most reliable transcoding signal
            transcoding = sum(
                1
                for s in active_sessions
                if s.get("TranscodingInfo")
                or s.get("PlayState", {}).get("PlayMethod") == "Transcode"
            )

            # Fetch library folders
            req = urllib.request.Request(f"{url}/Library/MediaFolders", headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                lib_data = json.loads(resp.read())

            libraries = [
                {"name": item.get("Name", ""), "type": item.get("CollectionType", "")}
                for item in lib_data.get("Items", [])
            ]

            if active == 0:
                summary = "Idle"
            else:
                summary = f"{active} stream{'s' if active != 1 else ''}"
                if transcoding:
                    summary += f" ({transcoding} transcoding)"

            status = "error" if False else ("warn" if transcoding else "ok")

            return PanelData(
                status=status,
                summary=summary,
                detail={
                    "streams": [
                        {
                            "title": s.get("NowPlayingItem", {}).get("Name", ""),
                            "user": s.get("UserName", ""),
                            "transcoding": bool(
                                s.get("TranscodingInfo")
                                or s.get("PlayState", {}).get("PlayMethod") == "Transcode"
                            ),
                        }
                        for s in active_sessions
                    ],
                    "transcoding_count": transcoding,
                    "libraries": libraries,
                },
            )
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    def frontend_js(self) -> str | None:
        return """
function render_jellyfin(data) {
  const streams = data.detail.streams || [];
  const libs = data.detail.libraries || [];
  let html = '';
  if (streams.length) {
    html += '<div style="margin-bottom:0.5rem">' + streams.map(s => {
      const color = s.transcoding ? 'var(--amber)' : 'var(--cyan)';
      return '<div style="font-size:0.55rem;padding:0.25rem 0.5rem;border:1px solid var(--border);border-radius:3px;margin-bottom:0.25rem"><span style="color:' + color + '">' + (s.transcoding ? '⚡' : '▶') + '</span> <strong>' + s.title + '</strong><span style="color:var(--text-dim);margin-left:0.3rem">· ' + s.user + '</span></div>';
    }).join('') + '</div>';
  } else {
    html += '<div style="font-size:0.6rem;color:var(--text-dim);margin-bottom:0.4rem">No active streams</div>';
  }
  if (libs.length) {
    html += '<div style="display:flex;flex-wrap:wrap;gap:0.3rem">' + libs.map(l =>
      '<div style="font-size:0.5rem;padding:0.15rem 0.4rem;border:1px solid var(--border);border-radius:3px;color:var(--text-dim)">' + l.name + '</div>'
    ).join('') + '</div>';
  }
  return html || '<div style="font-size:0.6rem;color:var(--text-dim)">No data</div>';
}
"""
