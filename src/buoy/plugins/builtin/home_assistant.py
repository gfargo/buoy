"""Home Assistant plugin — entity/automation counts, unavailable entities, updates."""

from __future__ import annotations

import json
import ssl
import urllib.request

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_MAX_LISTED = 10


class HomeAssistantPlugin(Plugin):
    """Shows Home Assistant entity/automation health and available updates."""

    manifest = PluginManifest(
        id="home_assistant",
        name="Home Assistant",
        icon="🏠",
        description="Entity/automation counts, unavailable entities, updates",
        version="1.0.0",
        config_schema={
            "url": {"type": "string", "required": True},
            "token": {"type": "string", "required": True},
            "verify_ssl": {"type": "boolean", "default": True},
        },
        refresh_interval=120,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "").rstrip("/")
        token = self.config.get("token", "")
        verify_ssl = self.config.get("verify_ssl", True)

        if not url or not token:
            return PanelData(status="disabled", summary="Not configured")

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        try:
            ctx = None
            if not verify_ssl:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

            config_req = urllib.request.Request(f"{url}/api/config", headers=headers)
            with urllib.request.urlopen(config_req, timeout=8, context=ctx) as resp:
                config = json.loads(resp.read())
            version = config.get("version")

            states_req = urllib.request.Request(f"{url}/api/states", headers=headers)
            with urllib.request.urlopen(states_req, timeout=8, context=ctx) as resp:
                states = json.loads(resp.read())

            automations = [s for s in states if s.get("entity_id", "").startswith("automation.")]
            unavailable = [s for s in states if s.get("state") in ("unavailable", "unknown")]
            disabled_automations = [a for a in automations if a.get("state") == "off"]
            updates = [
                s
                for s in states
                if s.get("entity_id", "").startswith("update.") and s.get("state") == "on"
            ]

            total = len(states)
            summary = f"{total} entities · {len(automations)} automations"
            if unavailable:
                summary += f" · {len(unavailable)} unavailable"
            if updates:
                summary += f" · {len(updates)} update{'s' if len(updates) != 1 else ''}"

            status = "warn" if (unavailable or updates or disabled_automations) else "ok"

            return PanelData(
                status=status,
                summary=summary,
                detail={
                    "version": version,
                    "total": total,
                    "automation_count": len(automations),
                    "unavailable": [s.get("entity_id", "") for s in unavailable[:_MAX_LISTED]],
                    "unavailable_count": len(unavailable),
                    # HA's REST API doesn't expose per-automation failure state;
                    # "disabled" (state == off) is the closest available proxy.
                    "disabled_automations": [
                        a.get("entity_id", "") for a in disabled_automations[:_MAX_LISTED]
                    ],
                    "disabled_automation_count": len(disabled_automations),
                    "updates": [s.get("entity_id", "") for s in updates[:_MAX_LISTED]],
                    "update_count": len(updates),
                },
            )
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    def demo_data(self) -> PanelData:
        return PanelData(
            status="warn",
            summary="182 entities · 24 automations · 1 unavailable · 1 update",
            detail={
                "version": "2026.8.1",
                "total": 182,
                "automation_count": 24,
                "unavailable": ["sensor.garage_temp"],
                "unavailable_count": 1,
                "disabled_automations": ["automation.vacation_mode"],
                "disabled_automation_count": 1,
                "updates": ["update.home_assistant_core"],
                "update_count": 1,
            },
        )

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        blocks: list[dict] = [
            panel.keyvalue(
                [
                    ("Entities", str(d.get("total", 0))),
                    ("Automations", str(d.get("automation_count", 0))),
                    ("Version", d.get("version") or "unknown"),
                ]
            )
        ]

        unavailable = d.get("unavailable") or []
        updates = d.get("updates") or []
        disabled_automations = d.get("disabled_automations") or []

        if unavailable:
            blocks.append(
                panel.list_(
                    [
                        panel.list_item(entity_id, secondary="unavailable", status="error")
                        for entity_id in unavailable
                    ]
                )
            )

        if updates:
            blocks.append(
                panel.list_(
                    [
                        panel.list_item(entity_id, secondary="update available", status="warn")
                        for entity_id in updates
                    ]
                )
            )

        if disabled_automations:
            blocks.append(
                panel.list_(
                    [
                        panel.list_item(entity_id, secondary="disabled", status="dim")
                        for entity_id in disabled_automations
                    ]
                )
            )

        if not unavailable and not updates and not disabled_automations:
            blocks.append(panel.text("All healthy", status="dim"))

        return blocks
