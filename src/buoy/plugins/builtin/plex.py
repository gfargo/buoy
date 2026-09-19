"""Plex plugin — media server status: active sessions, transcoding, library counts."""

from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.request
from typing import Any

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_ACTIVE_STATES = {"playing", "buffering"}


def _decision_label(value: str) -> str:
    if value == "transcode":
        return "transcode"
    if value == "copy":
        return "direct stream"
    return "direct play"


def _is_transcoding(session: dict[str, Any]) -> bool:
    """A session counts as transcoding only when video or audio is actually
    being transcoded — Plex also creates a TranscodeSession for direct-stream
    (remux) requests, so presence alone over-counts."""
    ts = session.get("TranscodeSession") or {}
    return ts.get("videoDecision") == "transcode" or ts.get("audioDecision") == "transcode"


def _session_row(session: dict[str, Any]) -> dict[str, Any]:
    ts = session.get("TranscodeSession") or {}
    player = session.get("Player") or {}
    user = session.get("User") or {}

    title = session.get("title", "")
    if session.get("grandparentTitle"):
        title = f"{session['grandparentTitle']} - {title}"

    duration = session.get("duration")
    offset = session.get("viewOffset")
    progress = None
    if isinstance(duration, int | float) and duration and isinstance(offset, int | float):
        progress = round(offset / duration * 100, 1)

    return {
        "title": title,
        "user": user.get("title", ""),
        "player": player.get("product") or player.get("title", ""),
        "state": player.get("state", ""),
        "transcoding": _is_transcoding(session),
        "video_decision": _decision_label(ts.get("videoDecision", "")),
        "audio_decision": _decision_label(ts.get("audioDecision", "")),
        "progress": progress,
    }


class PlexPlugin(Plugin):
    """Shows Plex media server status: active sessions, transcoding, library counts."""

    manifest = PluginManifest(
        id="plex",
        name="Plex",
        icon="🎞️",
        description="Plex sessions, transcoding, and library counts",
        version="1.0.0",
        config_schema={
            "url": {"type": "string", "required": True},
            # X-Plex-Token is sent as a header, never a query param
            "token": {"type": "string", "required": True, "secret": True},
            "verify_ssl": {"type": "boolean", "default": True},
            "library_counts": {"type": "boolean", "default": True},
            "transcode_warn_threshold": {"type": "integer", "default": 1},
            "max_rows": {"type": "integer", "default": 10},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "").rstrip("/")
        token = self.config.get("token", "")
        if not url or not token:
            return PanelData(status="disabled", summary="Not configured")

        verify_ssl = self.config.get("verify_ssl", True)
        ctx = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        loop = asyncio.get_running_loop()
        try:
            sessions, server_version, sections = await loop.run_in_executor(
                None, self._fetch_base, url, token, ctx
            )
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return PanelData(status="error", summary="Invalid token", detail={"error": str(e)})
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

        libraries: list[dict[str, Any]] = []
        if sections and self.config.get("library_counts", True):
            counts = await asyncio.gather(
                *(
                    loop.run_in_executor(None, self._fetch_library_count, url, token, ctx, s)
                    for s in sections
                ),
                return_exceptions=True,
            )
            for section, count in zip(sections, counts, strict=True):
                libraries.append(
                    {
                        "title": section.get("title", ""),
                        "type": section.get("type", ""),
                        "count": None if isinstance(count, BaseException) else count,
                    }
                )
        else:
            libraries = [
                {"title": s.get("title", ""), "type": s.get("type", ""), "count": None}
                for s in sections
            ]

        return self._build_panel(sessions, libraries, server_version)

    def _get_json(self, full_url: str, token: str, ctx: ssl.SSLContext | None) -> Any:
        req = urllib.request.Request(
            full_url, headers={"X-Plex-Token": token, "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            return json.loads(resp.read())

    def _fetch_base(
        self, url: str, token: str, ctx: ssl.SSLContext | None
    ) -> tuple[list[dict[str, Any]], str | None, list[dict[str, Any]]]:
        sessions_data = self._get_json(f"{url}/status/sessions", token, ctx)
        sessions = ((sessions_data.get("MediaContainer") or {}).get("Metadata")) or []

        server_version = None
        try:
            identity = self._get_json(f"{url}/identity", token, ctx)
            server_version = (identity.get("MediaContainer") or {}).get("version")
        except Exception:
            pass  # best-effort; version is a nice-to-have, not required

        sections_data = self._get_json(f"{url}/library/sections", token, ctx)
        sections = ((sections_data.get("MediaContainer") or {}).get("Directory")) or []

        return sessions, server_version, sections

    def _fetch_library_count(
        self, url: str, token: str, ctx: ssl.SSLContext | None, section: dict[str, Any]
    ) -> int:
        key = section.get("key")
        data = self._get_json(
            f"{url}/library/sections/{key}/all?X-Plex-Container-Start=0&X-Plex-Container-Size=0",
            token,
            ctx,
        )
        return int((data.get("MediaContainer") or {}).get("totalSize", 0))

    def _build_panel(
        self,
        sessions: list[dict[str, Any]],
        libraries: list[dict[str, Any]],
        server_version: str | None,
    ) -> PanelData:
        warn_threshold = int(self.config.get("transcode_warn_threshold", 1))
        max_rows = int(self.config.get("max_rows", 10))

        rows = [_session_row(s) for s in sessions]
        active_rows = [r for r in rows if r["state"] in _ACTIVE_STATES]
        paused_rows = [r for r in rows if r["state"] == "paused"]
        transcoding_count = sum(1 for r in active_rows if r["transcoding"])
        paused_count = len(paused_rows)

        bandwidths = [
            s["Session"]["bandwidth"]
            for s in sessions
            if isinstance(s.get("Session"), dict)
            and isinstance(s["Session"].get("bandwidth"), int | float)
        ]
        bandwidth_kbps = int(sum(bandwidths)) if bandwidths else None

        active = len(active_rows)
        if active == 0:
            summary = "Idle"
        else:
            summary = f"{active} stream{'s' if active != 1 else ''}"
            if transcoding_count:
                summary += f" ({transcoding_count} transcoding)"
        if paused_count:
            summary += f" · {paused_count} paused"

        status = "warn" if transcoding_count >= warn_threshold and warn_threshold > 0 else "ok"

        return PanelData(
            status=status,
            summary=summary,
            detail={
                "sessions": rows[:max_rows],
                "transcoding_count": transcoding_count,
                "paused_count": paused_count,
                "bandwidth_kbps": bandwidth_kbps,
                "libraries": libraries[:max_rows],
                "server_version": server_version,
            },
        )

    def demo_data(self) -> PanelData:
        sessions = [
            {
                "title": "The Matrix",
                "user": "alex",
                "player": "Chrome",
                "state": "playing",
                "transcoding": True,
                "video_decision": "transcode",
                "audio_decision": "direct play",
                "progress": 42.0,
            },
            {
                "title": "Breaking Bad - Ozymandias",
                "user": "sam",
                "player": "Plex for TV",
                "state": "paused",
                "transcoding": False,
                "video_decision": "direct play",
                "audio_decision": "direct play",
                "progress": 78.0,
            },
        ]
        libraries = [
            {"title": "Movies", "type": "movie", "count": 842},
            {"title": "TV Shows", "type": "show", "count": 156},
            {"title": "Music", "type": "artist", "count": 3021},
        ]
        return PanelData(
            status="warn",
            summary="1 stream (1 transcoding) · 1 paused",
            detail={
                "sessions": sessions,
                "transcoding_count": 1,
                "paused_count": 1,
                "bandwidth_kbps": 8200,
                "libraries": libraries,
                "server_version": "1.40.1.1234",
            },
        )

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        sessions = d.get("sessions") or []
        libraries = d.get("libraries") or []
        transcoding_count = d.get("transcoding_count", 0)
        paused_count = d.get("paused_count", 0)
        bandwidth_kbps = d.get("bandwidth_kbps")

        active_count = sum(1 for s in sessions if s.get("state") in _ACTIVE_STATES)

        kv_rows: list[dict[str, Any]] = [{"label": "Streams", "value": str(active_count)}]
        if transcoding_count:
            kv_rows.append(
                {"label": "Transcoding", "value": str(transcoding_count), "status": "warn"}
            )
        if paused_count:
            kv_rows.append({"label": "Paused", "value": str(paused_count)})
        if bandwidth_kbps is not None:
            kv_rows.append({"label": "Bandwidth", "value": f"{bandwidth_kbps / 1000:.1f} Mbps"})

        blocks: list[dict] = [panel.keyvalue(kv_rows)]

        if sessions:
            blocks.append(
                panel.list_(
                    [
                        panel.list_item(
                            f"{'⚡' if s.get('transcoding') else '▶' if s.get('state') == 'playing' else '⏸'} "
                            f"{s.get('title', '')}",
                            secondary=f"{s.get('user', '')} · {s.get('player', '')}",
                            status="warn" if s.get("transcoding") else "info",
                        )
                        for s in sessions
                    ]
                )
            )
        else:
            blocks.append(panel.text("No active streams", status="dim"))

        if libraries:
            blocks.append(
                panel.badges(
                    [
                        panel.badge(
                            f"{lib.get('title', '')} {lib.get('count')}"
                            if lib.get("count") is not None
                            else lib.get("title", ""),
                            status="dim",
                            dot=False,
                        )
                        for lib in libraries
                    ]
                )
            )

        return blocks

    def render_detail(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        sessions = d.get("sessions") or []
        libraries = d.get("libraries") or []

        blocks: list[dict] = []

        if sessions:
            rows = [
                [
                    panel.cell(s.get("title", ""), truncate=True),
                    panel.cell(s.get("user", "")),
                    panel.cell(s.get("player", "")),
                    panel.cell(
                        s.get("state", ""), status="warn" if s.get("state") == "paused" else None
                    ),
                    panel.cell(
                        "transcode" if s.get("transcoding") else s.get("video_decision", ""),
                        status="warn" if s.get("transcoding") else None,
                    ),
                    panel.cell(f"{s.get('progress')}%" if s.get("progress") is not None else "—"),
                ]
                for s in sessions
            ]
            blocks.append(
                panel.table(["Title", "User", "Player", "State", "Decision", "Progress"], rows)
            )
        else:
            blocks.append(panel.text("No active streams", status="dim"))

        if libraries:
            lib_rows = [
                [
                    panel.cell(lib.get("title", "")),
                    panel.cell(lib.get("type", "")),
                    panel.cell(str(lib.get("count")) if lib.get("count") is not None else "—"),
                ]
                for lib in libraries
            ]
            blocks.append(panel.table(["Library", "Type", "Items"], lib_rows))

        kv_rows = []
        server_version = d.get("server_version")
        if server_version:
            kv_rows.append({"label": "Server version", "value": server_version})
        url = self.config.get("url", "")
        if url:
            kv_rows.append({"label": "Server", "value": url, "href": url})
        if kv_rows:
            blocks.append(panel.keyvalue(kv_rows))

        return blocks
