"""Immich plugin — photo library stats from Immich photo server."""

from __future__ import annotations

import json
import ssl
import urllib.request

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class ImmichPlugin(Plugin):
    """Shows photo library stats from Immich."""

    manifest = PluginManifest(
        id="immich",
        name="Photos",
        icon="📷",
        description="Immich photo library stats",
        version="1.0.0",
        config_schema={
            "url": {"type": "string", "required": True},
            "api_key": {"type": "string", "required": True},
        },
        refresh_interval=300,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "")
        api_key = self.config.get("api_key", "")

        if not url or not api_key:
            return PanelData(status="disabled", summary="Not configured")

        try:
            base = url.rstrip("/")
            headers = {"x-api-key": api_key, "Accept": "application/json"}
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            stats_req = urllib.request.Request(f"{base}/api/server/statistics", headers=headers)
            with urllib.request.urlopen(stats_req, timeout=10, context=ctx) as resp:
                stats = json.loads(resp.read())

            storage_req = urllib.request.Request(f"{base}/api/server/storage", headers=headers)
            with urllib.request.urlopen(storage_req, timeout=10, context=ctx) as resp:
                storage = json.loads(resp.read())

            photos = stats.get("photos", 0)
            videos = stats.get("videos", 0)
            usage_bytes = stats.get("usage", 0)

            disk_pct = storage.get("diskUsagePercentage")
            if disk_pct is None:
                disk_use_raw = storage.get("diskUseRaw", 0)
                disk_size_raw = storage.get("diskSizeRaw", 0)
                disk_pct = round((disk_use_raw / disk_size_raw) * 100, 1) if disk_size_raw else 0
            else:
                disk_pct = round(disk_pct, 1)

            disk_use = storage.get("diskUse", "")
            disk_size = storage.get("diskSize", "")

            status = "warn" if disk_pct > 80 else "ok"

            return PanelData(
                status=status,
                summary=f"{photos:,} photos · {videos:,} videos · {disk_pct}% disk",
                detail={
                    "photos": photos,
                    "videos": videos,
                    "usage_bytes": usage_bytes,
                    "disk_pct": disk_pct,
                    "disk_use": disk_use,
                    "disk_size": disk_size,
                },
            )
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        pct = d.get("disk_pct") or 0
        bar_status = "warn" if pct > 80 else "info"
        label = f"{pct}% disk used"
        if d.get("disk_use"):
            label += f" · {d['disk_use']} / {d.get('disk_size', '')}"

        return [
            panel.keyvalue(
                [
                    ("Photos", f"{d.get('photos', 0):,}"),
                    ("Videos", f"{d.get('videos', 0):,}"),
                ]
            ),
            panel.bar(pct, label=label, status=bar_status),
        ]
